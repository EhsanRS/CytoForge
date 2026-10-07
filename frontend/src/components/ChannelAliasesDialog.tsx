import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, Plus, Trash2 } from "lucide-react";
import { api, post } from "../api";
import type { Channel, Sample, Workspace } from "../types";
import { acquisitionChannels, channelLabel } from "../types";
import { Modal } from "./Common";

type Row = { key: number; name: string; sources: Record<string, string> };
type Review = {
  revision: number;
  review_hash: string;
  changed: boolean;
  samples: {
    sample_id: string;
    sample_name: string;
    bindings: {
      name: string;
      source: string;
      previous?: string;
      action: string;
      references?: string[];
    }[];
  }[];
  affected_models: { id: string; name: string }[];
};

function sources(workspace: Workspace, sample: Sample): Channel[] {
  const acquired = acquisitionChannels(sample);
  const matrix = workspace.compensations.find(
    (m) => m.id === sample.compensation_id,
  );
  const outputs = matrix?.kind === "spectral" ? matrix.outputs : [];
  return [
    ...acquired,
    ...sample.channels.filter((c) => outputs?.includes(c.name)),
  ];
}

export function ChannelAliasesDialog({
  workspace,
  selected,
  onApplied,
  onClose,
}: {
  workspace: Workspace;
  selected: string[];
  onApplied: (doc: Workspace) => void;
  onClose: () => void;
}) {
  const samples = selected
    .map((id) => workspace.samples.find((s) => s.id === id))
    .filter((s): s is Sample => !!s);
  const [rows, setRows] = useState<Row[]>(() =>
    [...new Set(samples.flatMap((s) => Object.keys(s.aliases ?? {})))].map(
      (name, key) => ({
        key,
        name,
        sources: Object.fromEntries(
          samples.map((s) => [
            s.id,
            s.aliases?.[name] ??
              (sources(workspace, s).some((c) => c.name === name) ? name : ""),
          ]),
        ),
      }),
    ),
  );
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision] = useState(workspace.revision);
  const [outdated, setOutdated] = useState(false);
  const stale = outdated || workspace.revision !== revision;
  const base = `/workspaces/${workspace.id}/channel-aliases`;
  useEffect(() => {
    let stopped = false;
    const check = async () => {
      try {
        const current = await api<{ revision: number }>(
          `/workspaces/${workspace.id}/revision`,
        );
        if (!stopped && current.revision !== revision) setOutdated(true);
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    };
    void check();
    const timer = window.setInterval(() => void check(), 600);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [workspace.id, revision]);
  const close = useCallback(() => {
    if (!busy) onClose();
  }, [busy, onClose]);
  const edit = (next: Row[]) => {
    setRows(next);
    setReview(null);
    setError("");
  };
  const body = () => ({
    revision,
    mappings: samples.map((sample) => ({
      sample_id: sample.id,
      bindings: rows.map((row) => ({
        name: row.name.trim(),
        source: row.sources[sample.id],
      })),
    })),
  });
  const suggest = () => {
    const labels = new Set(
      samples.flatMap((s) => sources(workspace, s).map((c) => c.label.trim())),
    );
    const suggestions: Row[] = [];
    for (const label of labels) {
      if (!label || rows.some((r) => r.name === label)) continue;
      const matched = samples.map((s) =>
        sources(workspace, s).filter((c) => c.label.trim() === label),
      );
      if (
        matched.some((channels) => channels.length !== 1) ||
        matched.every((channels) => channels[0].name === label) ||
        samples.some((s, i) =>
          s.channels.some(
            (c) =>
              c.name === label &&
              !(label in (s.aliases ?? {})) &&
              c.name !== matched[i][0].name,
          ),
        )
      )
        continue;
      suggestions.push({
        key: Date.now() + suggestions.length,
        name: label,
        sources: Object.fromEntries(
          samples.map((s, i) => [s.id, matched[i][0].name]),
        ),
      });
    }
    if (!suggestions.length) {
      setError(
        "No unique marker labels match across these samples. Add a shared parameter and choose its source in each sample.",
      );
      return;
    }
    edit([...rows, ...suggestions].slice(0, 128));
  };
  const prepare = async () => {
    setBusy(true);
    setError("");
    try {
      setReview(await post<Review>(`${base}/preview`, body()));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (!review || stale) return;
    setBusy(true);
    setError("");
    try {
      const doc = await post<Workspace>(`${base}/apply`, {
        ...body(),
        review_hash: review.review_hash,
      });
      onApplied(doc);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Harmonize panel"
      subtitle={`${samples.length} selected samples · shared parameter names for gating and analysis`}
      onClose={close}
      wide
    >
      <p className="hint">
        Each shared name points to an original detector or active unmixed
        parameter. Choose its source in every sample. Existing aliases are
        included below; removing a row removes that alias after review.
      </p>
      {!review ? (
        <>
          {rows.map((row) => (
            <div className="panel channel-alias-row" key={row.key}>
              <div className="channel-alias-name">
                <label className="field">
                  Shared parameter name
                  <input
                    aria-label={`Shared name ${row.key}`}
                    value={row.name}
                    maxLength={160}
                    disabled={busy || stale}
                    onChange={(e) =>
                      edit(
                        rows.map((r) =>
                          r.key === row.key
                            ? { ...r, name: e.target.value }
                            : r,
                        ),
                      )
                    }
                  />
                </label>
                <button
                  className="button small"
                  aria-label={`Remove shared parameter ${row.name || "row"}`}
                  disabled={busy || stale}
                  onClick={() => edit(rows.filter((r) => r.key !== row.key))}
                >
                  <Trash2 size={14} /> Remove row
                </button>
              </div>
              <div className="channel-alias-sources">
                {samples.map((sample) => {
                  const available = sources(workspace, sample);
                  const chosen = row.sources[sample.id];
                  return (
                    <label className="field" key={sample.id}>
                      {sample.name}
                      <select
                        aria-label={`Source for ${row.name || "new parameter"} in ${sample.name}`}
                        value={chosen ?? ""}
                        disabled={busy || stale}
                        onChange={(e) =>
                          edit(
                            rows.map((r) =>
                              r.key === row.key
                                ? {
                                    ...r,
                                    sources: {
                                      ...r.sources,
                                      [sample.id]: e.target.value,
                                    },
                                  }
                                : r,
                            ),
                          )
                        }
                      >
                        <option value="">Choose original parameter…</option>
                        {chosen &&
                          !available.some((c) => c.name === chosen) && (
                            <option value={chosen}>
                              {chosen} · inactive unmixed output
                            </option>
                          )}
                        {available.map((channel) => (
                          <option value={channel.name} key={channel.name}>
                            {channelLabel(channel)}
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
          {!rows.length && (
            <p>No shared aliases are defined for these samples.</p>
          )}
          <div className="row-actions">
            <button
              className="button small"
              disabled={busy || stale || rows.length >= 128}
              onClick={() =>
                edit([...rows, { key: Date.now(), name: "", sources: {} }])
              }
            >
              <Plus size={14} /> Add shared parameter
            </button>
            <button
              className="button small"
              disabled={busy || stale || rows.length >= 128}
              onClick={suggest}
            >
              Suggest from marker labels
            </button>
          </div>
          <p className="hint">
            Suggestions use exact, unique marker-label matches. Review every
            source before applying. Original event columns and matrix detector
            names stay intact.
          </p>
        </>
      ) : (
        <div aria-label="Channel mapping review">
          {review.samples.map((sample) => (
            <div key={sample.sample_id}>
              <h3>{sample.sample_name}</h3>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Shared name</th>
                    <th>Original source</th>
                    <th>Change</th>
                  </tr>
                </thead>
                <tbody>
                  {sample.bindings.map((binding) => (
                    <tr key={binding.name}>
                      <td>{binding.name}</td>
                      <td>{binding.source}</td>
                      <td>
                        {binding.action}
                        {binding.previous &&
                          binding.previous !== binding.source &&
                          ` · previously ${binding.previous}`}
                        {!!binding.references?.length && (
                          <small>{binding.references.join("; ")}</small>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          {!!review.affected_models.length && (
            <p className="hint">
              These fitted analyses will become stale and need to be rerun:{" "}
              {review.affected_models.map((m) => m.name).join(", ")}.
            </p>
          )}
          <p>
            Apply saves all selected mappings in one undoable change. Rebound
            aliases change the values used by their saved populations and
            reports.
          </p>
        </div>
      )}
      {stale && (
        <p className="form-error" role="alert">
          Workspace changed. Close this dialog and reopen it to review the
          current mapping.
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-footer">
        <button className="button" disabled={busy} onClick={close}>
          Cancel
        </button>
        {review ? (
          <>
            <button
              className="button"
              disabled={busy || stale}
              onClick={() => setReview(null)}
            >
              Edit mapping
            </button>
            <button
              className="button primary"
              disabled={busy || stale || !review.changed}
              onClick={() => void apply()}
            >
              {busy && <LoaderCircle className="spin" size={14} />}
              Apply channel aliases
            </button>
          </>
        ) : (
          <button
            className="button primary"
            disabled={busy || stale || !samples.length}
            onClick={() => void prepare()}
          >
            {busy && <LoaderCircle className="spin" size={14} />}
            Review mapping
          </button>
        )}
      </div>
    </Modal>
  );
}
