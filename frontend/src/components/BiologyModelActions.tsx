import { useState } from "react";
import { Check, Pencil, RefreshCw, Trash2, X } from "lucide-react";
import { api } from "../api";
import type {
  CellCycleResult,
  ProliferationResult,
  KineticsResult,
  Workspace,
} from "../types";
import type { Commit } from "./Editors";

interface RemovalPreview {
  name: string;
  revision: number;
  review_hash: string;
  outputs: { sample_id: string; sample: string; parameters: string[] }[];
  derived_parameters: { sample_id: string; sample: string; name: string }[];
  gates: { id: string; sample_id: string; name: string }[];
  layout_plots: {
    layout_id: string;
    layout: string;
    plot_id: string;
    title: string;
  }[];
  layout_elements?: {
    layout_id: string;
    layout: string;
    element_id: string;
    kind: string;
    title: string;
  }[];
  tables: { id: string; name: string }[];
  plates?: { id: string; name: string }[];
  historical_analyses: { id: string; name: string }[];
}

export function BiologyModelActions({
  workspace,
  result,
  commit,
  busy,
  onReplace,
  onRemoved,
  onError,
}: {
  workspace: Workspace;
  result: CellCycleResult | ProliferationResult | KineticsResult;
  commit: Commit;
  busy: boolean;
  onReplace: () => void;
  onRemoved: () => void;
  onError: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(result.request.name);
  const [preview, setPreview] = useState<RemovalPreview | null>(null);
  const [cascade, setCascade] = useState(false);
  const [loading, setLoading] = useState(false);
  const active = workspace.samples.some((s) =>
    s.computed_parameters.some((p) => p.analysis_id === result.id),
  );
  const platform =
    result.request.algorithm === "cell_cycle"
      ? "cell-cycle"
      : result.request.algorithm;
  const path = `/biology/${platform}/${result.id}`;
  const count = preview
    ? preview.gates.length +
      preview.derived_parameters.length +
      preview.layout_plots.length +
      (preview.layout_elements?.length ?? 0) +
      preview.tables.length +
      (preview.plates?.length ?? 0)
    : 0;
  async function act(action: () => Promise<unknown>) {
    onError("");
    setLoading(true);
    try {
      await action();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  return (
    <div className="biology-model-actions">
      <div className="button-group population-exports">
        <button
          className="button small ghost"
          disabled={busy || loading || !active}
          onClick={onReplace}
        >
          <RefreshCw size={14} />
          Refit and replace
        </button>
        <button
          className="button small ghost"
          disabled={busy || loading}
          onClick={() => {
            setName(result.request.name);
            setEditing((v) => !v);
            setPreview(null);
          }}
        >
          <Pencil size={14} />
          Rename model
        </button>
        <button
          className="button small ghost"
          disabled={busy || loading}
          onClick={() =>
            act(async () => {
              setPreview(
                await api<RemovalPreview>(
                  `/workspaces/${workspace.id}${path}/dependencies`,
                ),
              );
              setCascade(false);
              setEditing(false);
            })
          }
        >
          <Trash2 size={14} />
          Remove model
        </button>
      </div>
      {!active && (
        <p className="muted small">
          Historical fit: its report and original event probabilities remain
          available. Refit settings creates a new model.
        </p>
      )}
      {editing && (
        <form
          className="population-save"
          onSubmit={(event) => {
            event.preventDefault();
            void act(async () => {
              await commit(path, { name }, "Renamed biological model", "PATCH");
              setEditing(false);
            });
          }}
        >
          <label className="field">
            Saved model name
            <input
              aria-label="Saved biological model name"
              required
              maxLength={125}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div className="button-group">
            <button
              className="button small primary"
              disabled={busy || loading}
              type="submit"
            >
              <Check size={14} />
              Save model name
            </button>
            <button
              className="button small ghost"
              type="button"
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
          </div>
          <p className="muted small">
            Parameter identifiers stay stable; labels and automatically named
            populations follow the new name.
          </p>
        </form>
      )}
      {preview && (
        <div
          className="population-save"
          role="region"
          aria-label="Review biological model removal"
        >
          <h4>Remove {preview.name}</h4>
          <p>
            Remove{" "}
            {preview.outputs.reduce((n, r) => n + r.parameters.length, 0)}{" "}
            output parameters from {preview.outputs.length} samples.
          </p>
          {!!preview.gates.length && (
            <details open>
              <summary>
                {preview.gates.length} populations, including descendants and
                Boolean dependents
              </summary>
              <ul>
                {preview.gates.map((g) => (
                  <li key={g.id}>
                    {workspace.samples.find((s) => s.id === g.sample_id)?.name}{" "}
                    · {g.name}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!!preview.derived_parameters.length && (
            <details open>
              <summary>
                {preview.derived_parameters.length} derived parameters
              </summary>
              <ul>
                {preview.derived_parameters.map((p, i) => (
                  <li key={i}>
                    {p.sample} · {p.name}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!!preview.layout_plots.length && (
            <p>
              {preview.layout_plots.length} affected plots will be removed from
              their layouts.
            </p>
          )}
          {!!preview.layout_elements?.length && (
            <details open>
              <summary>
                {preview.layout_elements.length} report objects are affected
              </summary>
              <ul>
                {preview.layout_elements.map((element) => (
                  <li key={`${element.layout_id}:${element.element_id}`}>
                    {element.layout} · {element.title || element.kind} ·{" "}
                    {element.kind === "biology"
                      ? "figure remains visibly unavailable"
                      : "removed with dependent sources"}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!!preview.tables.length && (
            <p>
              {preview.tables.length} tables are affected. Overview selections
              are cleared; custom columns retain their definitions and show
              unavailable inputs.
            </p>
          )}
          {!!preview.plates?.length && (
            <p>
              {preview.plates.length} plates are affected. Their measurement
              definitions stay available and show missing model inputs.
            </p>
          )}
          {!!preview.historical_analyses.length && (
            <p>
              {preview.historical_analyses.length} downstream or historical
              analyses stay available; their current input compatibility is
              shown when reviewed.
            </p>
          )}
          {count > 0 && (
            <label className="checkbox">
              <input
                aria-label="Remove dependent biological objects"
                type="checkbox"
                checked={cascade}
                onChange={(e) => setCascade(e.target.checked)}
              />
              Remove the listed dependent populations, parameters and report
              selections.
            </label>
          )}
          <p className="muted small">
            Undo restores the model and these objects. Original event data and
            fit assets are retained for project recovery.
          </p>
          <div className="button-group">
            <button
              className="button small danger"
              disabled={busy || loading || (count > 0 && !cascade)}
              onClick={() =>
                act(async () => {
                  await commit(
                    path,
                    { cascade, review_hash: preview.review_hash },
                    "Removed biological model",
                    "DELETE",
                  );
                  setPreview(null);
                  onRemoved();
                })
              }
            >
              <Trash2 size={14} />
              Confirm model removal
            </button>
            <button
              className="button small ghost"
              onClick={() => setPreview(null)}
            >
              <X size={14} />
              Cancel removal
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
