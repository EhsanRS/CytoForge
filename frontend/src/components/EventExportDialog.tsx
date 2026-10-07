import { useCallback, useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { api, download, post } from "../api";
import type { Sample, Workspace } from "../types";
import { formatNumber } from "../types";
import { Modal } from "./Common";

type Session = {
  id: string;
  revision: number;
  request: {
    format: "fcs" | "csv";
    values: "raw" | "compensated" | "scale";
    gate_id: string | null;
    scope?: import("../types").PooledScope;
  };
  status:
    "queued" | "running" | "ready" | "cancelled" | "failed" | "interrupted";
  stage: string;
  events_written: number;
  event_total: number;
  error: string | null;
  can_download: boolean;
  stale: boolean;
  summary: {
    event_count: number;
    channel_count: number;
    values: "raw" | "compensated" | "scale";
    matrix_preserved: boolean;
    exact_event_origins: boolean;
    alias_count?: number;
    format: "fcs" | "csv";
  } | null;
};
const active = (session: Session) =>
  ["queued", "running"].includes(session.status);

export function EventExportDialog({
  workspace,
  sample,
  gateId,
  onClose,
  onSaved,
  pooledScope,
}: {
  workspace: Workspace;
  sample: Sample;
  gateId: string | null;
  onClose: () => void;
  onSaved: () => void;
  pooledScope?: import("../types").PooledScope;
}) {
  const [format, setFormat] = useState<"fcs" | "csv">("fcs"),
    [values, setValues] = useState<"raw" | "compensated" | "scale">(
      pooledScope ? "compensated" : "raw",
    ),
    [population, setPopulation] = useState(gateId ?? ""),
    [session, setSession] = useState<Session | null>(null),
    [busy, setBusy] = useState(false),
    [closing, setClosing] = useState(false),
    [prepared, setPrepared] = useState<Session[]>([]),
    [error, setError] = useState("");
  const base = `/workspaces/${workspace.id}/event-exports`;
  const stale =
    !!session && (session.stale || session.revision !== workspace.revision);
  useEffect(() => {
    let stopped = false;
    api<Session[]>(`${base}?sample_id=${sample.id}`)
      .then((files) => {
        if (!stopped)
          setPrepared(
            files.filter(
              (file) =>
                !!file.request.scope === !!pooledScope &&
                file.request.scope?.group_id === pooledScope?.group_id &&
                file.request.scope?.sample_filter ===
                  pooledScope?.sample_filter,
            ),
          );
      })
      .catch((e: Error) => {
        if (!stopped) setError(e.message);
      });
    return () => {
      stopped = true;
    };
  }, [base, sample.id, pooledScope]);
  useEffect(() => {
    if (!session || (!active(session) && session.status !== "ready")) return;
    let stopped = false;
    const timer = setInterval(() => {
      api<Session>(`${base}/${session.id}`)
        .then((result) => {
          if (stopped) return;
          setSession(result);
          if (closing && !active(result)) onClose();
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
    if (busy || closing) return;
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
  }, [busy, closing, session, base, onClose]);
  async function prepare() {
    setBusy(true);
    setError("");
    try {
      setSession(
        await post<Session>(base, {
          revision: workspace.revision,
          sample_id: sample.id,
          gate_id: population || null,
          format,
          values,
          ...(pooledScope
            ? { scope: { ...pooledScope, gate_id: population || null } }
            : {}),
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (!session || stale || !session.can_download) return;
    setBusy(true);
    setError("");
    try {
      if (window.cytoforgeDesktop) {
        const result = await window.cytoforgeDesktop.saveEventExport({
          workspaceId: workspace.id,
          exportId: session.id,
        });
        if (result.canceled) return;
      } else
        await download(
          `${base}/${session.id}/download`,
          `population.${format}`,
        );
      await post(`${base}/${session.id}/cancel`, {});
      onSaved();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Export events"
      subtitle={pooledScope ? "Pooled group population" : sample.name}
      onClose={close}
    >
      {pooledScope && (
        <p>
          Export matching populations from every group member. The file records
          each original sample and event index. Stored values require a
          compatible shared correction; compensated values use each sample's own
          correction.
        </p>
      )}
      {!session ? (
        <div className="form-grid">
          <label>
            Population
            <select
              aria-label="Export population"
              value={population}
              onChange={(e) => setPopulation(e.target.value)}
              disabled={busy}
            >
              <option value="">All events</option>
              {workspace.gates
                .filter((g) => g.sample_id === sample.id)
                .map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            File format
            <select
              aria-label="Event export format"
              value={format}
              onChange={(e) => setFormat(e.target.value as typeof format)}
              disabled={busy}
            >
              <option value="fcs">FCS · 64-bit measurements</option>
              <option value="csv">CSV · numeric measurements</option>
            </select>
          </label>
          <label>
            Measurements
            <select
              aria-label="Export measurement values"
              value={values}
              onChange={(e) => setValues(e.target.value as typeof values)}
              disabled={busy}
            >
              <option value="raw">Stored measurements</option>
              <option value="compensated">Compensated / unmixed values</option>
              <option value="scale">Display scale values</option>
            </select>
          </label>
          <p className="muted">
            {format === "fcs"
              ? "FCS preserves sample tags, display settings, and source history when reopened in CytoForge. Stored measurements also preserve the assigned correction matrix."
              : "CSV contains numeric event rows. Choose FCS to retain correction and source history when reopening."}
            {sample.concatenation &&
              " Source and event identifiers remain exact integers in every measurement mode."}
          </p>
          {prepared.length > 0 && (
            <div>
              <p>
                <strong>Prepared files</strong>
              </p>
              {prepared.map((file) => (
                <p key={file.id}>
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() => {
                      setFormat(file.request.format);
                      setValues(file.request.values);
                      setPopulation(file.request.gate_id ?? "");
                      setSession(file);
                      setError("");
                    }}
                  >
                    Resume {file.request.format.toUpperCase()} export
                  </button>{" "}
                  {file.summary
                    ? `${formatNumber(file.summary.event_count)} events`
                    : file.stage}
                  {file.stale &&
                    " · workspace changed; discard to prepare again"}
                </p>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="form-grid" aria-live="polite">
          <p>
            {active(session) && <LoaderCircle className="spin" size={16} />}{" "}
            {session.stage}
          </p>
          {active(session) && (
            <>
              <progress
                max={Math.max(1, session.event_total)}
                value={session.events_written}
              />
              <p className="muted">
                {formatNumber(session.events_written)} /{" "}
                {formatNumber(session.event_total)} events
              </p>
            </>
          )}
          {session.summary && (
            <>
              <p>
                <strong>
                  {formatNumber(session.summary.event_count)} events
                </strong>{" "}
                · {session.summary.channel_count} parameters ·{" "}
                {session.summary.format.toUpperCase()}
              </p>
              <p>
                {session.summary.values === "raw"
                  ? "Stored measurements"
                  : session.summary.values === "compensated"
                    ? "Compensated / unmixed values"
                    : "Display scale values"}
              </p>
              {session.summary.matrix_preserved && (
                <p>Correction matrix preserved.</p>
              )}
              {session.summary.exact_event_origins && (
                <p>Exact event origins and source history preserved.</p>
              )}
              {!!session.summary.alias_count && (
                <p>
                  {session.summary.alias_count} shared channel aliases
                  preserved.
                </p>
              )}
            </>
          )}
          {stale && (
            <p className="form-error" role="alert">
              The workspace changed. Cancel and prepare the export again.
            </p>
          )}
          {session.error && (
            <p className="form-error" role="alert">
              {session.error}
            </p>
          )}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-footer">
        <button
          className="button"
          onClick={() => void close()}
          disabled={busy || closing}
        >
          {closing ? "Cancelling…" : "Cancel"}
        </button>
        {!session ? (
          <button
            className="button primary"
            onClick={() => void prepare()}
            disabled={busy}
          >
            {busy ? "Preparing…" : "Prepare export"}
          </button>
        ) : session.status === "ready" ? (
          <button
            className="button primary"
            onClick={() => void save()}
            disabled={busy || stale || !session.can_download}
          >
            {busy ? "Saving…" : "Save event file"}
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
              Edit export
            </button>
          )
        )}
      </div>
    </Modal>
  );
}
