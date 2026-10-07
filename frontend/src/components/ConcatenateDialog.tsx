import { useCallback, useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { api, download, post } from "../api";
import type { Sample, Workspace } from "../types";
import { formatNumber } from "../types";
import { Modal } from "./Common";

type Values = "raw" | "compensated" | "scale";
type Parameter = {
  name: string;
  enabled: boolean;
  sources: Record<string, string>;
};
type Session = {
  id: string;
  revision: number;
  status:
    | "queued"
    | "running"
    | "ready"
    | "applying"
    | "applied"
    | "cancelled"
    | "failed"
    | "interrupted";
  stage: string;
  events_written: number;
  event_total: number;
  samples: Sample[];
  error: string | null;
  review_hash: string | null;
  stale: boolean;
  can_apply: boolean;
};
const active = (session: Session) =>
  ["queued", "running", "applying"].includes(session.status);
function channels(sample: Sample, values: Values) {
  const virtual = new Set([
    ...sample.derived_parameters.map((p) => p.name),
    ...sample.computed_parameters.map((p) => p.name),
    ...sample.unmixed_parameters,
    ...Object.entries(sample.aliases ?? {})
      .filter(([, source]) => sample.unmixed_parameters.includes(source))
      .map(([name]) => name),
  ]);
  return sample.channels.filter(
    (c) => values !== "raw" || !virtual.has(c.name),
  );
}
function defaults(samples: Sample[], values: Values): Parameter[] {
  const used = samples.map(() => new Set<string>());
  const first = samples[0];
  const candidates = first ? channels(first, values) : [];
  return [
    ...candidates.filter((c) => c.name in (first?.aliases ?? {})),
    ...candidates.filter((c) => !(c.name in (first?.aliases ?? {}))),
  ]
    .filter((c) =>
      samples.every((s) => channels(s, values).some((p) => p.name === c.name)),
    )
    .filter((c) => !["CF_Source", "CF_EventID"].includes(c.name))
    .filter((c) => {
      const sources = samples.map((s) => s.aliases?.[c.name] ?? c.name);
      if (sources.some((source, i) => used[i].has(source))) return false;
      sources.forEach((source, i) => used[i].add(source));
      return true;
    })
    .map((c) => ({
      name: c.name,
      enabled: true,
      sources: Object.fromEntries(samples.map((s) => [s.id, c.name])),
    }));
}

export function ConcatenateDialog({
  workspace,
  selected,
  onCreated,
  onClose,
}: {
  workspace: Workspace;
  selected: string[];
  onCreated: (workspace: Workspace) => void;
  onClose: () => void;
}) {
  const samples = selected
    .map((id) => workspace.samples.find((s) => s.id === id))
    .filter((s): s is Sample => !!s);
  const [name, setName] = useState("Merged populations"),
    [values, setValues] = useState<Values>("raw"),
    [preserve, setPreserve] = useState(true),
    [parameters, setParameters] = useState(() => defaults(samples, "raw")),
    [populations, setPopulations] = useState<Record<string, string>>({}),
    [grouping, setGrouping] = useState("all"),
    [batch, setBatch] = useState(8),
    [groupKeyword, setGroupKeyword] = useState("donor"),
    [keywords, setKeywords] = useState<string[]>([]),
    [session, setSession] = useState<Session | null>(null),
    [busy, setBusy] = useState(false),
    [closing, setClosing] = useState(false),
    [error, setError] = useState("");
  const base = `/workspaces/${workspace.id}/concatenations`;
  const keywordNames = [
    ...new Set(
      samples.flatMap((s) => [
        ...Object.keys(s.tags),
        ...Object.keys(s.metadata),
      ]),
    ),
  ]
    .filter((k) => k.length <= 157)
    .sort();
  const stale =
    !!session && (session.stale || session.revision !== workspace.revision);
  useEffect(() => {
    if (!session || !active(session)) return;
    let stopped = false;
    const timer = setInterval(() => {
      api<Session>(`${base}/${session.id}`)
        .then((value) => {
          if (stopped) return;
          setSession(value);
          if (closing && !active(value)) onClose();
        })
        .catch((e: Error) => {
          if (!stopped) setError(e.message);
        });
    }, 350);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [session?.id, session?.status, base, closing, onClose]);
  const close = useCallback(async () => {
    if (busy || session?.status === "applying") return;
    if (session && (active(session) || session.status === "ready")) {
      setClosing(true);
      try {
        const result = await post<Session>(`${base}/${session.id}/cancel`, {});
        setSession(result);
        if (!active(result)) onClose();
      } catch (e) {
        setError((e as Error).message);
        setClosing(false);
      }
    } else onClose();
  }, [busy, session, base, onClose]);
  const prepare = async () => {
    setBusy(true);
    setError("");
    try {
      setSession(
        await post<Session>(base, {
          revision: workspace.revision,
          name,
          inputs: samples.map((s) => ({
            sample_id: s.id,
            gate_id: populations[s.id] || null,
          })),
          parameters: parameters
            .filter((p) => p.enabled)
            .map(({ name, sources }) => ({ name, sources })),
          values,
          compensation: preserve ? "preserve" : "discard",
          grouping,
          batch_size: batch,
          group_keyword: groupKeyword || null,
          keywords,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const create = async () => {
    if (!session) return;
    setBusy(true);
    setError("");
    try {
      onCreated(
        await post<Workspace>(`${base}/${session.id}/apply`, {
          revision: session.revision,
          review_hash: session.review_hash,
        }),
      );
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const populationName = (id: string): string => {
    const gate = workspace.gates.find((g) => g.id === id);
    return gate
      ? (gate.parent_id ? populationName(gate.parent_id) + " / " : "") +
          gate.name
      : "";
  };
  const updateParameter = (index: number, change: Partial<Parameter>) =>
    setParameters((rows) =>
      rows.map((p, i) => (i === index ? { ...p, ...change } : p)),
    );
  return (
    <Modal
      title="Concatenate populations"
      subtitle="Create merged samples with exact source event identity"
      onClose={close}
      wide
    >
      {(error || session?.error) && (
        <p className="form-error" role="alert">
          {error || session?.error}
        </p>
      )}
      {!session ? (
        <>
          <label className="field">
            Output sample name
            <input
              aria-label="Output sample name"
              value={name}
              maxLength={160}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Source sample</th>
                  <th>Population</th>
                </tr>
              </thead>
              <tbody>
                {samples.map((s) => (
                  <tr key={s.id}>
                    <td>{s.name}</td>
                    <td>
                      <select
                        aria-label={`Population for ${s.name}`}
                        value={populations[s.id] || ""}
                        onChange={(e) =>
                          setPopulations({
                            ...populations,
                            [s.id]: e.target.value,
                          })
                        }
                      >
                        <option value="">All events</option>
                        {workspace.gates
                          .filter((g) => g.sample_id === s.id)
                          .map((g) => (
                            <option key={g.id} value={g.id}>
                              {populationName(g.id)}
                            </option>
                          ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="form-grid">
            <label className="field">
              Values
              <select
                aria-label="Concatenation values"
                value={values}
                onChange={(e) => {
                  const value = e.target.value as Values;
                  setValues(value);
                  setParameters(defaults(samples, value));
                }}
              >
                <option value="raw">Raw acquired values</option>
                <option value="compensated">
                  Compensated / unmixed values
                </option>
                <option value="scale">Compensated display scale values</option>
              </select>
            </label>
            <label className="field">
              Outputs
              <select
                aria-label="Concatenation grouping"
                value={grouping}
                onChange={(e) => setGrouping(e.target.value)}
              >
                <option value="all">One combined sample</option>
                <option value="batch">One sample per N sources</option>
                <option value="keyword">One sample per keyword value</option>
              </select>
            </label>
            {grouping === "batch" && (
              <label className="field">
                Sources per output
                <input
                  aria-label="Sources per output"
                  type="number"
                  min={1}
                  max={128}
                  value={batch}
                  onChange={(e) => setBatch(Number(e.target.value))}
                />
              </label>
            )}
            {grouping === "keyword" && (
              <label className="field">
                Grouping keyword
                <select
                  aria-label="Grouping keyword"
                  value={groupKeyword}
                  onChange={(e) => setGroupKeyword(e.target.value)}
                >
                  <option value="">Choose keyword</option>
                  {keywordNames.map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {values === "raw" && (
            <label className="checkbox-row">
              <input
                type="checkbox"
                aria-label="Preserve shared compensation matrix"
                checked={preserve}
                onChange={(e) => setPreserve(e.target.checked)}
              />
              Preserve a shared spillover matrix as a new snapshot
            </label>
          )}
          <p className="form-note">
            Raw values preserve acquired measurements. Compensated values use
            each source’s assigned matrix and receive no matrix assignment.
            Scale values also apply each source parameter’s display transform.
          </p>
          <div className="panel-heading">
            <strong>Parameter matching</strong>
            <button
              className="button small"
              onClick={() =>
                setParameters([
                  ...parameters,
                  {
                    name: `Parameter ${parameters.length + 1}`,
                    enabled: true,
                    sources: Object.fromEntries(
                      samples.map((s) => [
                        s.id,
                        channels(s, values)[0]?.name || "",
                      ]),
                    ),
                  },
                ])
              }
            >
              Add parameter mapping
            </button>
          </div>
          <div className="table-scroll concat-parameters">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Include</th>
                  <th>Output parameter</th>
                  {samples.map((s) => (
                    <th key={s.id}>{s.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {parameters.map((p, index) => (
                  <tr key={index}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Include parameter ${index + 1}`}
                        checked={p.enabled}
                        onChange={(e) =>
                          updateParameter(index, { enabled: e.target.checked })
                        }
                      />
                    </td>
                    <td>
                      <input
                        aria-label={`Output parameter ${index + 1}`}
                        value={p.name}
                        maxLength={160}
                        onChange={(e) =>
                          updateParameter(index, { name: e.target.value })
                        }
                      />
                    </td>
                    {samples.map((s) => (
                      <td key={s.id}>
                        <select
                          aria-label={`Parameter ${index + 1} in ${s.name}`}
                          value={p.sources[s.id] || ""}
                          onChange={(e) =>
                            updateParameter(index, {
                              sources: { ...p.sources, [s.id]: e.target.value },
                            })
                          }
                        >
                          {channels(s, values).map((c) => (
                            <option key={c.name} value={c.name}>
                              {c.label ? `${c.name} · ${c.label}` : c.name}
                            </option>
                          ))}
                        </select>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <label className="field">
            Additional keyword columns
            <select
              aria-label="Additional keyword columns"
              multiple
              value={keywords}
              onChange={(e) =>
                setKeywords(
                  [...e.target.selectedOptions].map((o) => o.value).slice(0, 8),
                )
              }
            >
              {keywordNames.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          <p className="form-note">
            CF_Source and CF_EventID identify each source sample and its
            original event. Keyword columns contain category codes, with the
            codebook stored in sample provenance. Every selected event is
            retained.
          </p>
        </>
      ) : (
        <div
          className="sample-import-progress"
          role="status"
          aria-live="polite"
        >
          <strong>
            {active(session) && <LoaderCircle size={16} className="spin" />}{" "}
            {closing ? "Stopping concatenation" : session.stage}
          </strong>
          {active(session) && (
            <>
              <progress
                aria-label="Concatenation progress"
                max={Math.max(1, session.event_total)}
                value={session.event_total ? session.events_written : undefined}
              />
              <p>
                {formatNumber(session.events_written, 0)} of{" "}
                {formatNumber(session.event_total, 0)} events written
              </p>
            </>
          )}
          {session.status === "ready" && (
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>New sample</th>
                    <th>Events</th>
                    <th>Sources</th>
                    <th>Compensation</th>
                  </tr>
                </thead>
                <tbody>
                  {session.samples.map((s) => (
                    <tr key={s.id}>
                      <td>{s.name}</td>
                      <td>{formatNumber(s.event_count, 0)}</td>
                      <td>{s.concatenation?.sources.length}</td>
                      <td>
                        {s.compensation_id
                          ? "Shared matrix snapshot"
                          : "No assignment"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="form-note">
                The merged data is ready. Create samples adds this batch in one
                undoable change. The original samples and populations stay in
                the workspace.
              </p>
            </>
          )}
          {stale && (
            <p className="form-error" role="alert">
              The workspace changed. Discard this result and prepare it again.
            </p>
          )}
        </div>
      )}
      <div className="modal-footer">
        <button
          className="button"
          onClick={() => void close()}
          disabled={busy || closing || session?.status === "applying"}
        >
          {session ? "Discard / cancel" : "Cancel"}
        </button>
        {!session ? (
          <button
            className="button primary"
            onClick={() => void prepare()}
            disabled={
              busy ||
              !name.trim() ||
              !samples.length ||
              !parameters.some((p) => p.enabled)
            }
          >
            {busy ? "Preparing…" : "Prepare merge"}
          </button>
        ) : session.status === "ready" ? (
          <button
            className="button primary"
            onClick={() => void create()}
            disabled={busy || stale || !session.can_apply}
          >
            {busy ? "Creating samples…" : "Create samples"}
          </button>
        ) : (
          ["failed", "cancelled", "interrupted"].includes(session.status) && (
            <button
              className="button"
              onClick={() => {
                setSession(null);
                setError("");
              }}
            >
              Edit selection
            </button>
          )
        )}
      </div>
    </Modal>
  );
}

export function ConcatenationOrigins({
  workspace,
  sample,
  onClose,
}: {
  workspace: Workspace;
  sample: Sample;
  onClose: () => void;
}) {
  const [offset, setOffset] = useState(0),
    [rows, setRows] = useState<
      { event_id: string; sample_name: string; source_event_id: string }[]
    >([]),
    [error, setError] = useState("");
  useEffect(() => {
    let stopped = false;
    setRows([]);
    setError("");
    api<{ rows: typeof rows }>(
      `/workspaces/${workspace.id}/samples/${sample.id}/origins?offset=${offset}&limit=100`,
    )
      .then((data) => {
        if (!stopped) setRows(data.rows);
      })
      .catch((e: Error) => {
        if (!stopped) setError(e.message);
      });
    return () => {
      stopped = true;
    };
  }, [workspace.id, sample.id, offset]);
  return (
    <Modal
      title="Merged event origins"
      subtitle={sample.name}
      onClose={onClose}
      wide
    >
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <p className="form-note">
        {sample.concatenation?.values} values · source snapshot revision{" "}
        {sample.concatenation?.revision}. Event IDs are zero based. Original
        samples can be removed without losing this record.
      </p>
      <table className="data-table">
        <thead>
          <tr>
            <th>Source index</th>
            <th>Original sample</th>
            <th>Events retained</th>
          </tr>
        </thead>
        <tbody>
          {sample.concatenation?.sources.map((s) => (
            <tr key={s.index}>
              <td>{s.index}</td>
              <td>{s.sample_name}</td>
              <td>{formatNumber(s.count, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.entries(sample.concatenation?.keywords || {}).map(
        ([key, codes]) => (
          <p className="form-note" key={key}>
            CF_{key}:{" "}
            {codes
              .map((value, i) => `${i + 1} = ${value ?? "missing"}`)
              .join("; ")}
          </p>
        ),
      )}
      <div className="table-scroll concat-origins">
        <table className="data-table">
          <thead>
            <tr>
              <th>Merged event ID</th>
              <th>Source sample</th>
              <th>Source event ID</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.event_id}>
                <td>{row.event_id}</td>
                <td>{row.sample_name}</td>
                <td>{row.source_event_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="modal-footer">
        <button
          className="button"
          disabled={!offset}
          onClick={() => setOffset(Math.max(0, offset - 100))}
        >
          Previous events
        </button>
        <button
          className="button"
          disabled={offset + 100 >= sample.event_count}
          onClick={() => setOffset(offset + 100)}
        >
          Next events
        </button>
        <button
          className="button"
          onClick={() =>
            void download(
              `/workspaces/${workspace.id}/samples/${sample.id}/export/origins`,
              "event-origins.csv",
            ).catch((e: Error) => setError(e.message))
          }
        >
          Export event origins
        </button>
        <button className="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
