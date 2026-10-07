import { useState } from "react";
import { Download, FileCode2, LoaderCircle, Upload } from "lucide-react";
import { api, download, post } from "../api";
import type {
  InterchangeIssue,
  InterchangePreview,
  InterchangeRecord,
  Workspace,
} from "../types";
import { formatNumber } from "../types";
import { Modal } from "./Common";

function Issues({ issues }: { issues: InterchangeIssue[] }) {
  return (
    <div className="interchange-issues" aria-label="Import diagnostics">
      {issues.map((issue, index) => (
        <div key={index} className={`interchange-issue ${issue.severity}`}>
          <strong>
            {issue.severity === "error"
              ? "Not converted"
              : issue.severity === "info"
                ? "Information"
                : "Review"}
            {issue.gate ? ` · ${issue.gate}` : ""}
          </strong>
          <p>{issue.message}</p>
        </div>
      ))}
    </div>
  );
}

export function InterchangeDialog({
  workspace,
  onClose,
  onApply,
}: {
  workspace: Workspace;
  onClose: () => void;
  onApply: (workspace: Workspace) => void;
}) {
  const [preview, setPreview] = useState<InterchangePreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string[]>>({});
  const [display, setDisplay] = useState(true),
    [replace, setReplace] = useState(false);
  const [tables, setTables] = useState(true);
  const [keywords, setKeywords] = useState(true);
  const [acknowledged, setAcknowledged] = useState(false),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [record, setRecord] = useState<InterchangeRecord | null>(null);
  const base = `/workspaces/${workspace.id}/interchange`;
  const selected = Object.values(mapping).flat();
  const stale = !!preview && preview.revision !== workspace.revision;
  const load = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    setRecord(null);
    setAcknowledged(false);
    setPreview(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await api<InterchangePreview>(
        `${base}/preview?revision=${workspace.revision}`,
        { method: "POST", body: form },
      );
      setPreview(result);
      setMapping(
        Object.fromEntries(
          result.document.sources.map((source) => [
            source.id,
            source.suggested_sample_ids.length === 1
              ? source.suggested_sample_ids
              : [],
          ]),
        ),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      const updated = await post<Workspace>(`${base}/apply`, {
        revision: preview.revision,
        preview_id: preview.preview_id,
        mappings: Object.entries(mapping)
          .filter(([, ids]) => ids.length)
          .map(([source_id, sample_ids]) => ({ source_id, sample_ids })),
        include_display_settings: display,
        include_tables: tables,
        include_keywords: keywords,
        replace_gates: replace,
        allow_partial: acknowledged,
      });
      onApply(updated);
      setRecord(updated.interchanges.at(-1)!);
      setPreview(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const getFile = async (
    item: InterchangeRecord,
    kind: "source" | "report",
  ) => {
    try {
      await download(
        `${base}/${item.id}/${kind}`,
        kind === "source" ? item.name : `${item.name}.report.json`,
      );
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <Modal
      title="Import gates & workspaces"
      subtitle="FlowJo 10/11 (.wsp) and GatingML 2.0 (.xml)"
      wide
      onClose={busy ? () => {} : onClose}
    >
      <div className="interchange-upload">
        <FileCode2 size={28} />
        <div>
          <strong>Move an existing gating strategy into this workspace</strong>
          <p>
            Import the FCS samples first, then map each source tree to
            compatible samples. Source XML and the report stay with your
            project.
          </p>
        </div>
        <label className="button">
          <Upload size={15} /> Choose XML or WSP
          <input
            aria-label="Choose gate XML or FlowJo workspace"
            type="file"
            accept=".wsp,.xml"
            disabled={busy}
            onChange={(event) => {
              void load(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </label>
      </div>
      {busy && (
        <p className="info-strip">
          <LoaderCircle className="spin" size={16} /> Processing gate strategy…
        </p>
      )}
      {preview && (
        <>
          <div className="interchange-summary">
            <strong>{preview.document.name}</strong>
            <span>
              {preview.document.format === "flowjo" ? "FlowJo" : "GatingML"}{" "}
              {preview.document.version} · {preview.document.sources.length}{" "}
              source trees
            </span>
          </div>
          <div className="interchange-mappings">
            {preview.document.sources.map((source) => (
              <section key={source.id} className="interchange-source">
                <div>
                  <strong>{source.name}</strong>
                  <p>
                    {source.gates.length} of {source.total_gates} gates
                    supported · {source.matrices.length} matrices
                    {source.event_count != null
                      ? ` · ${formatNumber(source.event_count, 0)} source events`
                      : ""}
                  </p>
                </div>
                <fieldset>
                  <legend>Target samples</legend>
                  {workspace.samples
                    .filter((sample) =>
                      source.compatible_sample_ids.includes(sample.id),
                    )
                    .map((sample) => (
                      <label className="checkbox-field" key={sample.id}>
                        <input
                          type="checkbox"
                          checked={
                            mapping[source.id]?.includes(sample.id) ?? false
                          }
                          disabled={
                            busy ||
                            (!source.gates.length &&
                              !(
                                tables &&
                                preview.document.tables.some(
                                  (t) =>
                                    t.definition &&
                                    t.source_ids.includes(source.id),
                                )
                              ))
                          }
                          onChange={(event) =>
                            setMapping({
                              ...mapping,
                              [source.id]: event.target.checked
                                ? [...(mapping[source.id] ?? []), sample.id]
                                : (mapping[source.id] ?? []).filter(
                                    (id) => id !== sample.id,
                                  ),
                            })
                          }
                        />
                        {sample.name}
                        {source.event_count != null &&
                        source.event_count !== sample.event_count
                          ? ` · ${formatNumber(sample.event_count, 0)} events (different count)`
                          : ""}
                      </label>
                    ))}
                  {!source.compatible_sample_ids.length && (
                    <p className="form-note">
                      No compatible sample. Required channels:{" "}
                      {source.channels.join(", ") || "none"}.
                    </p>
                  )}
                </fieldset>
                <details>
                  <summary>Review gate tree & coordinates</summary>
                  <div className="interchange-tree">
                    {source.gates.map((gate) => (
                      <div key={gate.id}>
                        <strong>{gate.name}</strong>
                        <span>
                          {gate.kind}
                          {gate.complement ? " · outside" : ""}
                          {gate.dimensions
                            ?.map(
                              (dim) =>
                                ` · ${dim.ratio_channels ? dim.ratio_channels.join(" / ") : dim.channel} [${dim.minimum ?? "−∞"}, ${dim.maximum ?? "+∞"}) ${dim.transform.kind}`,
                            )
                            .join("")}
                          {gate.kind === "boolean"
                            ? ` · ${gate.operation.toUpperCase()}`
                            : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                </details>
              </section>
            ))}
          </div>
          {preview.document.tables.some((t) => t.total_columns > 0) && (
            <section
              className="interchange-source"
              aria-label="Saved FlowJo tables"
            >
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={tables}
                  onChange={(event) => {
                    setTables(event.target.checked);
                    if (!event.target.checked)
                      setMapping((previous) =>
                        Object.fromEntries(
                          preview.document.sources.map((s) => [
                            s.id,
                            s.gates.length ? (previous[s.id] ?? []) : [],
                          ]),
                        ),
                      );
                  }}
                />
                Import supported saved tables
              </label>
              {preview.document.tables
                .filter((t) => t.total_columns > 0)
                .map((table) => (
                  <details key={table.id}>
                    <summary>
                      {table.name} · {table.definition?.columns.length ?? 0} of{" "}
                      {table.total_columns} columns supported
                    </summary>
                    <p className="form-note">
                      {table.group_name === "workspaceSelection"
                        ? "Mapped samples"
                        : table.group_name}{" "}
                      · source population and compensation bindings are
                      retained.
                    </p>
                    {table.definition?.columns.map((column) => (
                      <p key={column.id} className="form-note">
                        {column.name} ·{" "}
                        {column.kind === "formula"
                          ? column.expression
                          : column.kind === "metadata"
                            ? column.metadata_key
                            : column.statistic}
                      </p>
                    ))}
                  </details>
                ))}
            </section>
          )}
          <Issues issues={preview.document.issues} />
          {preview.document.format === "flowjo" && (
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={keywords}
                onChange={(event) => setKeywords(event.target.checked)}
              />
              Add missing source annotations; keep existing target values
            </label>
          )}
          {preview.document.format === "flowjo" && (
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={display}
                onChange={(event) => setDisplay(event.target.checked)}
              />
              Use source display transforms and sample compensation
            </label>
          )}
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={replace}
              onChange={(event) => setReplace(event.target.checked)}
            />
            Replace existing gates in selected target samples
          </label>
          {preview.requires_acknowledgement && (
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
              I reviewed the report and accept the listed conversion limits and
              calculation differences.
            </label>
          )}
          <p className="form-note">
            Gate coordinates keep their explicit compensation and transforms.
            Source paths are used for matching; this import does not open files
            at those paths.
          </p>
          {stale && (
            <p className="form-error">
              Workspace changed. Choose the source again to prepare a current
              preview.
            </p>
          )}
          {new Set(selected).size !== selected.length && (
            <p className="form-error">
              Map each target sample to only one source tree.
            </p>
          )}
        </>
      )}
      {record && (
        <section className="interchange-result">
          <strong>{record.gate_ids.length} gates imported</strong>
          {!!record.table_ids?.length && (
            <p>
              {record.table_ids.length} saved tables imported · open Statistics
              to review them.
            </p>
          )}
          <Issues issues={record.report.issues} />
          <details>
            <summary>Compare population counts</summary>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Population</th>
                    <th>Source</th>
                    <th>CytoForge</th>
                  </tr>
                </thead>
                <tbody>
                  {record.report.counts.map((row) => (
                    <tr key={row.id}>
                      <td>{row.name}</td>
                      <td>{formatNumber(row.reported_count, 0)}</td>
                      <td>{formatNumber(row.count, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <button
            className="button small"
            onClick={() => void getFile(record, "report")}
          >
            <Download size={14} />
            Download report
          </button>
        </section>
      )}
      {!preview && !!workspace.interchanges?.length && (
        <div className="interchange-history">
          <h3>Saved import records</h3>
          {[...workspace.interchanges].reverse().map((item) => (
            <div className="interchange-summary" key={item.id}>
              <span>
                <strong>{item.name}</strong>
                <small>
                  {item.gate_ids.length} gates ·{" "}
                  {!!item.table_ids?.length &&
                    `${item.table_ids.length} tables · `}
                  {new Date(item.created_at).toLocaleString()}
                </small>
              </span>
              <button
                className="button small"
                onClick={() => void getFile(item, "source")}
              >
                Source XML
              </button>
              <button
                className="button small"
                onClick={() => void getFile(item, "report")}
              >
                Report
              </button>
            </div>
          ))}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-footer">
        <button className="button" disabled={busy} onClick={onClose}>
          {record ? "Done" : "Close"}
        </button>
        {preview && (
          <button
            className="button primary"
            disabled={
              busy ||
              stale ||
              !selected.length ||
              new Set(selected).size !== selected.length ||
              (preview.requires_acknowledgement && !acknowledged)
            }
            onClick={() => void apply()}
          >
            <Upload size={15} />
            {tables && preview.document.tables.some((table) => table.definition)
              ? "Import selected gates & tables"
              : "Import selected gates"}
          </button>
        )}
      </div>
    </Modal>
  );
}
