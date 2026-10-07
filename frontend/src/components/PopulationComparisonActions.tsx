import { useState } from "react";
import { Pencil, Trash2 } from "lucide-react";
import { api } from "../api";
import type { PopulationComparisonResult, Workspace } from "../types";
import type { Commit } from "./Editors";
import { Modal } from "./Common";

interface RemovalPreview {
  revision: number;
  name: string;
  review_hash: string;
  results: { id: string; name: string }[];
  tables: {
    id: string;
    name: string;
    columns: { id: string; name: string; effect: string }[];
  }[];
  layouts: {
    id: string;
    name: string;
    elements: { id: string; title: string; effect: string }[];
  }[];
}

export function PopulationComparisonActions({
  workspace,
  result,
  commit,
  busy,
  onRemoved,
  onError,
}: {
  workspace: Workspace;
  result: PopulationComparisonResult;
  commit: Commit;
  busy: boolean;
  onRemoved: () => void;
  onError: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(result.request.name);
  const [preview, setPreview] = useState<RemovalPreview | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const path = `/population-comparison/${result.id}`;
  const affected =
    !!preview &&
    (preview.results.length > 1 ||
      preview.tables.length > 0 ||
      preview.layouts.length > 0);
  const stale = preview?.revision !== workspace.revision;
  async function act(action: () => Promise<unknown>) {
    onError("");
    setError("");
    setLoading(true);
    try {
      await action();
    } catch (error) {
      setError((error as Error).message);
      onError((error as Error).message);
    } finally {
      setLoading(false);
    }
  }
  async function review() {
    setPreview(
      await api<RemovalPreview>(`/workspaces/${workspace.id}${path}/removal`),
    );
    setReviewed(false);
  }
  const effectLabel = (effect: string) =>
    effect === "previous_refit"
      ? "Returns to the previous retained refit"
      : "Becomes unavailable until restored or rebound";
  return (
    <>
      <button
        disabled={busy || loading}
        onClick={() => {
          setName(result.request.name);
          setEditing(true);
        }}
      >
        <Pencil size={14} />
        Rename comparison
      </button>
      <button disabled={busy || loading} onClick={() => void act(review)}>
        <Trash2 size={14} />
        Review removal
      </button>
      {editing && (
        <Modal
          title="Rename comparison"
          onClose={() => {
            if (!loading) setEditing(false);
          }}
        >
          {error && <div role="alert">{error}</div>}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void act(async () => {
                await commit(
                  `${path}/rename`,
                  { name: name.trim() },
                  "Renamed comparison",
                );
                setEditing(false);
              });
            }}
          >
            <label className="field">
              Name
              <input
                aria-label="Saved comparison name"
                maxLength={160}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <div className="button-row">
              <button
                type="button"
                disabled={loading}
                onClick={() => setEditing(false)}
              >
                Cancel
              </button>
              <button
                className="primary"
                disabled={busy || loading || !name.trim()}
              >
                Save name
              </button>
            </div>
          </form>
        </Modal>
      )}
      {preview && (
        <Modal
          title="Review comparison removal"
          subtitle={preview.name}
          onClose={() => {
            if (!loading) setPreview(null);
          }}
        >
          {error && <div role="alert">{error}</div>}
          <p>
            Remove this comparison and its later refits from the saved results.
            Undo restores the records and their bindings.
          </p>
          <ul>
            {preview.results.map((item) => (
              <li key={item.id}>
                {item.name} · {item.id.slice(0, 8)}
              </li>
            ))}
          </ul>
          {preview.tables.map((table) => (
            <div key={table.id}>
              <strong>Table · {table.name}</strong>
              <ul>
                {table.columns.map((column) => (
                  <li key={column.id}>
                    {column.name} · {effectLabel(column.effect)}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {preview.layouts.map((layout) => (
            <div key={layout.id}>
              <strong>Layout · {layout.name}</strong>
              <ul>
                {layout.elements.map((element) => (
                  <li key={element.id}>
                    {element.title || "Comparison figure"} ·{" "}
                    {effectLabel(element.effect)}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {affected && (
            <label className="checkbox">
              <input
                type="checkbox"
                checked={reviewed}
                onChange={(event) => setReviewed(event.target.checked)}
              />
              I reviewed the affected refits, table cells and figures.
            </label>
          )}
          {stale && (
            <p className="warning-banner">
              The workspace changed. Refresh the removal review.
            </p>
          )}
          <div className="button-row">
            <button disabled={loading} onClick={() => setPreview(null)}>
              Cancel
            </button>
            {stale && (
              <button
                disabled={busy || loading}
                onClick={() => void act(review)}
              >
                Refresh review
              </button>
            )}
            <button
              className="danger"
              disabled={busy || loading || stale || (affected && !reviewed)}
              onClick={() =>
                void act(async () => {
                  await commit(
                    path,
                    {
                      cascade: affected && reviewed,
                      review_hash: preview.review_hash,
                    },
                    "Removed comparison",
                    "DELETE",
                  );
                  setPreview(null);
                  onRemoved();
                })
              }
            >
              Remove reviewed results
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
