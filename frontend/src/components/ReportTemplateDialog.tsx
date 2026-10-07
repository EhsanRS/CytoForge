import { memo, useEffect, useMemo, useRef, useState } from "react";
import { FileUp, LoaderCircle, RefreshCw } from "lucide-react";
import { api, post } from "../api";
import {
  templateBindingLabels,
  templateOptionLabel,
  templateParentKinds,
  type ReportTemplate,
  type TemplateOption,
  type TemplateRequest,
  type TemplateReview,
} from "../reportTemplates";
import type { ReportRender, Workspace } from "../types";
import { id } from "../types";
import { Modal } from "./Common";

const ROOT = "__all_events__";
const BindingChoice = memo(function BindingChoice({
  row,
  options,
  value,
  staleParent,
  ownerLabel,
  onChange,
}: {
  row: TemplateReview["bindings"][number];
  options: TemplateOption[];
  value: string;
  staleParent: boolean;
  ownerLabel: string;
  onChange: (key: string, value: string) => void;
}) {
  const [search, setSearch] = useState("");
  const choices = useMemo(() => {
    const selected = options.find((option) => option.id === value);
    const matched = options.filter((option) =>
      templateOptionLabel(option)
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
    );
    const visible = matched.slice(0, 200);
    const duplicateLabels = new Set<string>();
    const labels = new Set<string>();
    for (const option of options) {
      const name = templateOptionLabel(option);
      if (labels.has(name)) duplicateLabels.add(name);
      labels.add(name);
    }
    if (selected && !visible.some((option) => option.id === value))
      visible.unshift(selected);
    return { visible, total: matched.length, duplicateLabels, selected };
  }, [options, search, value]);
  const label = templateBindingLabels[row.kind];
  return (
    <div className={`template-binding ${row.status}`}>
      <div>
        <span className="eyebrow">{label}</span>
        <strong>{row.population_path.join(" / ") || row.name}</strong>
        {ownerLabel && <small>{ownerLabel}</small>}
        {row.kind === "column" && (
          <small>
            {row.details.kind} · {row.details.channel || row.details.statistic}
          </small>
        )}
      </div>
      <div>
        {options.length > 200 && (
          <input
            aria-label={`Search destinations for ${row.name}`}
            placeholder="Search destinations"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        )}
        <select
          aria-label={`${label} destination for ${row.name}`}
          value={value}
          disabled={staleParent}
          onChange={(event) => onChange(row.key, event.target.value)}
        >
          <option value="">Choose destination</option>
          {row.kind === "population" && (
            <option value={ROOT}>All events (explicit choice)</option>
          )}
          {choices.visible.map((option) => (
            <option key={option.id} value={option.id}>
              {templateOptionLabel(option)}
              {choices.duplicateLabels.has(templateOptionLabel(option))
                ? ` · ${option.id.slice(-6)}`
                : ""}
            </option>
          ))}
        </select>
        {choices.selected?.details.channel && (
          <small>
            Selected parameter: {choices.selected.details.channel}
            {choices.selected.details.transform
              ? ` · ${choices.selected.details.transform}`
              : ""}
          </small>
        )}
        {staleParent ? (
          <small>Review bindings after changing its source.</small>
        ) : (
          choices.total > 200 && (
            <small>
              Search to find all {choices.total.toLocaleString()} matching
              destinations.
            </small>
          )
        )}
      </div>
    </div>
  );
});

export function ReportTemplateDialog({
  workspace,
  keepDraft,
  onClose,
  onApply,
}: {
  workspace: Workspace;
  keepDraft: boolean;
  onClose: () => void;
  onApply: (request: TemplateRequest) => Promise<void>;
}) {
  const [template, setTemplate] = useState<ReportTemplate | null>(null);
  const [review, setReview] = useState<TemplateReview | null>(null);
  const [figure, setFigure] = useState<ReportRender | null>(null);
  const [name, setName] = useState("");
  const [mappings, setMappings] = useState<Record<string, string | null>>({});
  const [scope, setScope] = useState<"destination" | "template">("destination");
  const [working, setWorking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [changed, setChanged] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [filename, setFilename] = useState("");
  const [previewPage, setPreviewPage] = useState(0);
  const proposal = useRef(id());
  const sequence = useRef(0);
  const base = `/workspaces/${workspace.id}`;
  useEffect(() => {
    sequence.current++;
    setReview(null);
    setFigure(null);
    setChanged(true);
    setWorking(false);
    return () => {
      sequence.current++;
    };
  }, [workspace.id]);
  const stale =
    !!review &&
    (review.workspace_id !== workspace.id ||
      review.revision !== workspace.revision);
  function payload(value: ReportTemplate): TemplateRequest {
    return {
      revision: workspace.revision,
      id: proposal.current,
      template: value,
      name: name.trim() || value.definition.name,
      mappings,
      batch_scope: scope,
      batch_sample_ids: [],
    };
  }
  async function reviewBindings(
    value = template,
    request = value ? payload(value) : null,
    renderPage = previewPage,
  ) {
    if (!value || !request) return;
    const serial = ++sequence.current;
    setWorking(true);
    setError("");
    setFigure(null);
    try {
      const result = await post<TemplateReview>(
        `${base}/report-templates/review`,
        request,
      );
      if (serial !== sequence.current) return;
      setReview(result);
      setChanged(false);
      if (result.can_apply && result.definition) {
        const definition = structuredClone(result.definition);
        definition.batch = {
          ...definition.batch,
          mode: "off",
          group_id: null,
          sample_ids: [],
          overrides: {},
          population_overrides: {},
        };
        const rendered = await post<ReportRender>(`${base}/reports/render`, {
          revision: result.revision,
          definition,
          prototype_page: Math.min(renderPage, definition.pages.length - 1),
          validate_sources: true,
        });
        if (serial === sequence.current) setFigure(rendered);
      }
    } catch (failure) {
      if (serial === sequence.current) setError((failure as Error).message);
    } finally {
      if (serial === sequence.current) setWorking(false);
    }
  }
  async function load(file: File | undefined) {
    if (!file) return;
    const serial = ++sequence.current;
    setWorking(true);
    setError("");
    setReview(null);
    setFigure(null);
    setTemplate(null);
    try {
      if (file.size > 8 * 1024 * 1024)
        throw new Error("Report template exceeds eight MiB");
      const form = new FormData();
      form.append("file", file);
      const value = await api<ReportTemplate>(
        `${base}/report-templates/parse?revision=${workspace.revision}`,
        { method: "POST", body: form },
      );
      if (serial !== sequence.current) return;
      proposal.current = id();
      setTemplate(value);
      setFilename(file.name);
      setName(`${value.definition.name.slice(0, 149)} (template)`);
      setMappings({});
      setScope("destination");
      setPreviewPage(0);
      setChanged(true);
      await reviewBindings(
        value,
        {
          revision: workspace.revision,
          id: proposal.current,
          template: value,
          name: `${value.definition.name.slice(0, 149)} (template)`,
          mappings: {},
          batch_scope: "destination",
          batch_sample_ids: [],
        },
        0,
      );
    } catch (failure) {
      if (serial === sequence.current) setError((failure as Error).message);
    } finally {
      if (serial === sequence.current) setWorking(false);
    }
  }
  function choose(key: string, value: string) {
    setMappings((previous) => {
      const next = { ...previous };
      if (!value) delete next[key];
      else
        next[key] =
          value === ROOT &&
          review?.bindings.find((row) => row.key === key)?.kind === "population"
            ? null
            : value;
      for (const row of review?.bindings ?? []) {
        const parent = templateParentKinds[row.kind];
        if (parent && `${parent}/${row.owner_id}` === key) delete next[row.key];
      }
      return next;
    });
    setChanged(true);
    setFigure(null);
  }
  async function apply() {
    if (!template || !review || changed || stale) return;
    setWorking(true);
    setApplying(true);
    setError("");
    try {
      await onApply({
        ...payload(template),
        revision: review.revision,
        review_hash: review.review_hash,
      });
      onClose();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setWorking(false);
      setApplying(false);
    }
  }
  const rows = (review?.bindings ?? []).filter(
    (row) =>
      row.active &&
      `${templateBindingLabels[row.kind]} ${row.name} ${row.population_path.join(" ")}`
        .toLocaleLowerCase()
        .includes(filter.toLocaleLowerCase()),
  );
  return (
    <Modal
      title="Load report template"
      subtitle="Reuse page design with this experiment's samples and saved results."
      onClose={() => {
        if (!applying) onClose();
      }}
      wide
    >
      <div className="report-template-dialog">
        <label className="button template-file-picker">
          <FileUp size={16} /> Choose template
          <input
            aria-label="Choose report template"
            type="file"
            accept=".json,.cytoforge-report"
            disabled={working}
            onChange={(event) => {
              void load(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </label>
        {filename && <p className="muted">{filename}</p>}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {template && (
          <>
            <label>
              Imported report name
              <input
                aria-label="Imported report name"
                value={name}
                disabled={working}
                maxLength={160}
                onChange={(event) => {
                  setName(event.target.value);
                  setChanged(true);
                  setFigure(null);
                }}
              />
            </label>
            {template.definition.batch.mode !== "off" && (
              <label>
                Batch acquisitions
                <select
                  aria-label="Template batch scope"
                  value={scope}
                  disabled={working}
                  onChange={(event) => {
                    setScope(event.target.value as typeof scope);
                    setChanged(true);
                    setFigure(null);
                  }}
                >
                  <option value="destination">
                    Use destination group or experiment
                  </option>
                  <option value="template">
                    Rebind the template's exact sample selection and overrides
                  </option>
                </select>
              </label>
            )}
            <p>
              Choose samples and saved sources, then review to match their
              populations and columns. Fixed controls retain their selected
              destination.
            </p>
            <input
              aria-label="Filter template bindings"
              placeholder="Filter sources, populations or parameters"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
            <div
              className="template-bindings"
              aria-label="Template source bindings"
            >
              {rows.map((row) => {
                const manual = Object.prototype.hasOwnProperty.call(
                  mappings,
                  row.key,
                );
                const chosen = manual ? mappings[row.key] : row.target;
                const parent = templateParentKinds[row.kind];
                const parentKey = parent ? `${parent}/${row.owner_id}` : "";
                const previousParent = review?.bindings.find(
                  (value) => value.key === parentKey,
                )?.target;
                const changedParent =
                  !!parentKey &&
                  Object.prototype.hasOwnProperty.call(mappings, parentKey) &&
                  mappings[parentKey] !== previousParent;
                const value =
                  chosen === null &&
                  (manual || row.status === "mapped") &&
                  row.kind === "population"
                    ? ROOT
                    : (chosen ?? "");
                return (
                  <BindingChoice
                    key={row.key}
                    row={row}
                    options={review?.targets[row.options_key ?? ""] ?? []}
                    value={value}
                    staleParent={working || changedParent}
                    ownerLabel={
                      review?.bindings.find((value) => value.key === parentKey)
                        ?.name ?? ""
                    }
                    onChange={choose}
                  />
                );
              })}
            </div>
            {review?.issues.length ? (
              <div className="template-issues" role="status">
                {review.issues.map((issue, index) => (
                  <p key={index}>{issue.message}</p>
                ))}
              </div>
            ) : null}
            {stale && (
              <p className="form-error">
                This experiment changed. Review current bindings before
                importing.
              </p>
            )}
            {review?.resets_batch_bindings && (
              <p className="muted">
                Acquisition-specific selections and overrides will be rebuilt
                for the destination cohort. Review the report's batch before
                export.
              </p>
            )}
            {review?.plan && !review.plan.exportable && (
              <p className="muted">
                The destination batch still has source issues. Its usual batch
                review will identify them before export.
              </p>
            )}
            {review?.definition && (
              <label>
                Preview page
                <select
                  aria-label="Template preview page"
                  value={previewPage}
                  disabled={working}
                  onChange={(event) => {
                    setPreviewPage(Number(event.target.value));
                    setChanged(true);
                    setFigure(null);
                  }}
                >
                  {review.definition.pages.map((_, index) => (
                    <option key={index} value={index}>
                      {index + 1}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {figure && (
              <div
                className="template-page-preview"
                aria-label="Template page preview"
                dangerouslySetInnerHTML={{ __html: figure.svg }}
              />
            )}
            {figure && !figure.exportable && (
              <p className="form-error">
                Resolve the preview's source issues before importing:{" "}
                {figure.issues.map((issue) => issue.message).join("; ")}
              </p>
            )}
            {keepDraft && (
              <p className="muted">
                Your unsaved report stays open. The imported report will appear
                in Saved report.
              </p>
            )}
          </>
        )}
        <div className="modal-actions">
          <button className="button" disabled={applying} onClick={onClose}>
            Cancel
          </button>
          <button
            className="button"
            disabled={!template || working || !name.trim()}
            onClick={() => void reviewBindings()}
          >
            {working ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <RefreshCw size={16} />
            )}{" "}
            Review bindings
          </button>
          <button
            className="button primary"
            disabled={
              working ||
              changed ||
              stale ||
              !review?.can_apply ||
              !figure?.exportable ||
              !name.trim()
            }
            onClick={() => void apply()}
          >
            {keepDraft
              ? "Import and keep current draft"
              : "Import and open report"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
