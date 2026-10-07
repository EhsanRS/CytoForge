import { useState, type FormEvent } from "react";
import {
  ArrowRight,
  Check,
  FlaskConical,
  GitBranch,
  Magnet,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import type {
  Compensation,
  Gate,
  GateDimension,
  MagneticResolution,
  Group,
  Sample,
  Workspace,
  Transform,
  TransformKind,
} from "../types";
import { acquisitionChannels, channelLabel, formatNumber, id } from "../types";
import { api } from "../api";
import { Modal, Tag } from "./Common";
import { CompensationWizard } from "./CompensationWizard";
import { AutoSpreadDialog } from "./AutoSpreadDialog";
import { GateShapeEditor } from "./GateShapeEditor";
import { partitionSize } from "../gatePartitions";
import { validateCurly, validateSpider } from "../gateGeometry";

export type Commit = (
  path: string,
  body: unknown,
  message: string,
  method?: string,
) => Promise<Workspace>;
export function CoordinateTransform({
  value,
  onChange,
}: {
  value: Transform;
  onChange: (value: Transform) => void;
}) {
  const keys: Partial<
    Record<TransformKind, (keyof Omit<Transform, "kind">)[]>
  > = {
    asinh: ["cofactor"],
    logicle: ["t", "w", "m", "a"],
    hyperlog: ["t", "w", "m", "a"],
    gml_linear: ["t", "a"],
    gml_log: ["t", "m"],
    gml_asinh: ["t", "m", "a"],
    wsp_log: ["offset", "m"],
    wsp_biex: ["positive", "negative", "width", "top"],
  };
  const defaults = {
    offset: 1,
    positive: 4.41854,
    negative: 0,
    width: -10,
    top: 262144.000029,
  };
  return (
    <>
      <label className="field">
        Coordinate scale
        <select
          aria-label="Coordinate scale"
          value={value.kind}
          onChange={(event) =>
            onChange({ ...value, kind: event.target.value as TransformKind })
          }
        >
          {[
            "linear",
            "log",
            "logicle",
            "hyperlog",
            "asinh",
            "gml_linear",
            "gml_log",
            "gml_asinh",
            "wsp_log",
            "wsp_biex",
          ].map((kind) => (
            <option key={kind} value={kind}>
              {kind}
            </option>
          ))}
        </select>
      </label>
      <div className="field-grid">
        {(keys[value.kind] ?? []).map((key) => (
          <label className="field" key={key}>
            {key.toUpperCase()}
            <input
              type="number"
              step="any"
              required
              value={value[key] ?? defaults[key as keyof typeof defaults]}
              onChange={(event) =>
                onChange({ ...value, [key]: Number(event.target.value) })
              }
            />
          </label>
        ))}
      </div>
      <div className="field-row">
        {(["bound_min", "bound_max"] as const).map((key) => (
          <label className="field grow" key={key}>
            {key === "bound_min"
              ? "Output clamp minimum"
              : "Output clamp maximum"}
            <input
              type="number"
              step="any"
              placeholder="Unbounded"
              value={value[key] ?? ""}
              onChange={(event) =>
                onChange({
                  ...value,
                  [key]:
                    event.target.value === ""
                      ? null
                      : Number(event.target.value),
                })
              }
            />
          </label>
        ))}
      </div>
    </>
  );
}
export function GateDialog({
  gate,
  workspace,
  existing,
  busy,
  baseRevision,
  onRebase,
  onClose,
  onSave,
  pooledScope,
}: {
  gate: Gate;
  workspace: Workspace;
  existing?: boolean;
  busy: boolean;
  baseRevision?: number | null;
  onRebase?: () => void;
  onClose: () => void;
  onSave: (gate: Gate) => Promise<unknown>;
  pooledScope?: import("../types").PooledScope;
}) {
  const [draft, setDraft] = useState(gate);
  const [vertices, setVertices] = useState(
    gate.vertices.map((p) => p.join(", ")).join("\n"),
  );
  const [holes, setHoles] = useState(
    (gate.holes ?? []).map((ring) => ring.map((p) => p.join(", ")).join("\n")),
  );
  const [error, setError] = useState("");
  const stale = baseRevision != null && workspace.revision !== baseRevision;
  const current = workspace.gates.find((value) => value.id === gate.id);
  const [coordinates, setCoordinates] = useState(
    gate.coordinates?.join(", ") ?? "",
  );
  const [covariance, setCovariance] = useState(
    gate.covariance?.map((row) => row.join(", ")).join("\n") ?? "",
  );
  const [magneticBusy, setMagneticBusy] = useState(false);
  const [shapeVisible, setShapeVisible] = useState(false);
  const [shapeActive, setShapeActive] = useState(false);
  const [magneticPreview, setMagneticPreview] = useState<{
    key: string;
    gate: Gate;
    magnetic: MagneticResolution;
  } | null>(null);
  const previewKey = JSON.stringify([
    draft,
    vertices,
    holes,
    coordinates,
    covariance,
    workspace.revision,
  ]);
  const preview = magneticPreview?.key === previewKey ? magneticPreview : null;
  const canMagnet =
    ["range", "rectangle", "ellipse", "polygon"].includes(draft.kind) ||
    ((draft.kind === "hyperrectangle" || draft.kind === "ellipsoid") &&
      !!draft.dimensions?.length &&
      draft.dimensions.length <= 2 &&
      (draft.kind === "ellipsoid" ||
        draft.dimensions.every(
          (dim) => dim.minimum !== null && dim.maximum !== null,
        )));
  const gates = workspace.gates.filter(
    (g) =>
      g.sample_id === gate.sample_id &&
      g.id !== gate.id &&
      (!gate.partition || g.partition?.id !== gate.partition.id),
  );
  const valueForSave = () => {
    const value = { ...draft, name: draft.name.trim() };
    if (value.kind === "ellipsoid") {
      value.coordinates = coordinates.split(",").map((v) => Number(v.trim()));
      value.covariance = covariance
        .trim()
        .split("\n")
        .map((row) => row.split(",").map((v) => Number(v.trim())));
      if (
        ![...value.coordinates, ...value.covariance.flat()].every(
          Number.isFinite,
        )
      )
        throw new Error(
          "Enter finite ellipsoid coordinates and covariance values",
        );
    }
    if (value.kind === "polygon") {
      const parseRing = (text: string) =>
        text
          .trim()
          .split("\n")
          .map((line) => {
            const parts = line.split(",");
            const pair = parts.map(Number);
            if (
              pair.length !== 2 ||
              parts.some((part) => !part.trim()) ||
              !pair.every(Number.isFinite)
            )
              throw new Error("Enter one X, Y coordinate pair per line");
            return pair as [number, number];
          });
      value.vertices = parseRing(vertices);
      value.holes = holes.map(parseRing);
      if ([value.vertices, ...value.holes].some((ring) => ring.length < 3))
        throw new Error("Each polygon ring needs at least three vertices");
      if (
        value.vertices.length +
          value.holes.reduce((n, ring) => n + ring.length, 0) >
        2000
      )
        throw new Error(
          "A polygon supports up to 2,000 vertices across all rings",
        );
    }
    if (
      ![...value.bounds, ...(value.center ?? []), ...(value.radii ?? [])].every(
        Number.isFinite,
      )
    )
      throw new Error("All coordinates must be valid numbers");
    if (value.spider) validateSpider(value.spider);
    if (value.curly) validateCurly(value.curly);
    return value;
  };
  let shapeDraft: Gate | null = null;
  let shapeError = "";
  if (shapeVisible) {
    try {
      shapeDraft = valueForSave();
    } catch (err) {
      shapeError = (err as Error).message;
    }
  }
  const updateVisualShape = (value: Gate) => {
    setDraft((current) => ({
      ...current,
      bounds: value.bounds,
      vertices: value.vertices,
      holes: value.holes,
      center: value.center,
      radii: value.radii,
      angle: value.angle,
      coordinates: value.coordinates,
      covariance: value.covariance,
      spider: value.spider,
      curly: value.curly,
      dimensions: current.dimensions?.map((dimension, i) => {
        const geometry = value.dimensions?.[i];
        return geometry
          ? {
              ...dimension,
              minimum: geometry.minimum,
              maximum: geometry.maximum,
            }
          : dimension;
      }),
    }));
    setVertices(value.vertices.map((p) => p.join(", ")).join("\n"));
    setHoles(
      (value.holes ?? []).map((ring) =>
        ring.map((p) => p.join(", ")).join("\n"),
      ),
    );
    setCoordinates(value.coordinates?.join(", ") ?? "");
    setCovariance(
      value.covariance?.map((row) => row.join(", ")).join("\n") ?? "",
    );
  };
  const previewMagnetic = async () => {
    setError("");
    setMagneticBusy(true);
    const key = previewKey;
    try {
      if (stale)
        throw new Error(
          "Review the changed workspace before previewing this draft.",
        );
      const result = await api<{ gate: Gate; magnetic: MagneticResolution }>(
        `/workspaces/${workspace.id}/gates/preview-magnetic`,
        {
          method: "POST",
          body: JSON.stringify({
            revision: workspace.revision,
            gate: valueForSave(),
          }),
        },
      );
      setMagneticPreview({ key, ...result });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setMagneticBusy(false);
    }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    try {
      if (stale)
        throw new Error(
          "Review the changed workspace before saving this draft.",
        );
      await onSave(valueForSave());
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const updateBound = (index: number, value: number) =>
    setDraft({
      ...draft,
      bounds: draft.bounds.map((v, i) => (i === index ? value : v)),
    });
  const updateDimension = (index: number, change: Partial<GateDimension>) =>
    setDraft({
      ...draft,
      dimensions: draft.dimensions?.map((dim, i) =>
        i === index ? { ...dim, ...change } : dim,
      ),
    });
  return (
    <Modal
      title={
        existing
          ? "Edit population"
          : gate.partition?.kind === "curly"
            ? "Create curly populations"
            : gate.partition?.kind === "spider"
              ? "Create spider populations"
              : gate.partition?.kind === "bisector"
                ? "Create bisector populations"
                : gate.kind === "quadrant" ||
                    gate.partition?.kind === "quadrant"
                  ? "Create quadrant populations"
                  : "Create population"
      }
      subtitle="Gates are evaluated against every event in the parent population."
      onClose={onClose}
      wide={shapeVisible}
    >
      <form onSubmit={submit}>
        {draft.partition && (
          <p className="form-note" role="note">
            {draft.curly
              ? "These four populations share their center, noise coefficients, parent and coordinate definitions. Saving changes them together. Every finite eligible parent event belongs to one population; exact boundaries belong to the positive side."
              : draft.spider
                ? "These four populations share a center, four arms, parent and coordinate definitions. Saving changes them together. Every finite parent event belongs to one sector. The center belongs to Q2; arms 1–4 belong to Q2, Q2, Q1 and Q3 respectively."
                : `These ${partitionSize(draft)} populations share their thresholds, parent and coordinate definitions. Saving changes them together. Boundary events belong to the positive side; intervals have no overlap.`}
          </p>
        )}
        {stale && (
          <div className="gate-draft-conflict" role="alert">
            <strong>The workspace changed while this gate was open.</strong>
            <p>
              Your unsaved draft is retained. Review the current population
              before choosing to save your draft over it.
            </p>
            {existing ? (
              current ? (
                <details>
                  <summary>Current saved population: {current.name}</summary>
                  <pre>
                    {JSON.stringify(
                      {
                        parent_id: current.parent_id,
                        kind: current.kind,
                        x: current.x,
                        y: current.y,
                        x_transform: current.x_transform,
                        y_transform: current.y_transform,
                        bounds: current.bounds,
                        vertices: current.vertices,
                        holes: current.holes,
                        center: current.center,
                        radii: current.radii,
                        dimensions: current.dimensions,
                        coordinates: current.coordinates,
                        covariance: current.covariance,
                        spider: current.spider,
                        curly: current.curly,
                        operands: current.operands,
                        magnetic: current.magnetic,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              ) : (
                <p>
                  This population has been removed. Restore it before saving, or
                  cancel this draft.
                </p>
              )
            ) : (
              <p>
                The current workspace is at revision {workspace.revision}. Check
                the parent population and coordinates before creating this gate.
              </p>
            )}
            {onRebase && (!existing || current) && (
              <button
                type="button"
                className="button small"
                onClick={() => {
                  setError("");
                  onRebase();
                }}
              >
                Keep draft and use current workspace
              </button>
            )}
          </div>
        )}
        {[
          "range",
          "rectangle",
          "polygon",
          "ellipse",
          "quadrant",
          "hyperrectangle",
          "ellipsoid",
          "spider",
          "curly",
        ].includes(draft.kind) &&
          (draft.dimensions?.length ?? 0) <= 2 && (
            <div className="visual-shape-control">
              <button
                className="button small"
                type="button"
                disabled={busy || stale || shapeActive}
                aria-expanded={shapeVisible}
                onClick={() => setShapeVisible(!shapeVisible)}
              >
                {shapeVisible ? "Hide visual editor" : "Edit shape visually"}
              </button>
            </div>
          )}
        {shapeVisible &&
          (shapeDraft ? (
            <GateShapeEditor
              gate={shapeDraft}
              workspace={workspace}
              disabled={busy || stale}
              onChange={updateVisualShape}
              onActiveChange={setShapeActive}
              pooledScope={pooledScope}
            />
          ) : (
            <p className="form-error" role="alert">
              {shapeError}
            </p>
          ))}
        <label className="field">
          Population name
          <input
            autoComplete="off"
            required
            maxLength={150}
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </label>
        <div className="field-row">
          <label className="field grow">
            Parent population
            <select
              value={draft.parent_id ?? ""}
              disabled={draft.kind === "membership"}
              onChange={(e) =>
                setDraft({ ...draft, parent_id: e.target.value || null })
              }
            >
              <option value="">All events</option>
              {gates.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Gate color
            <input
              type="color"
              value={draft.color}
              onChange={(e) => setDraft({ ...draft, color: e.target.value })}
            />
          </label>
        </div>
        <div className="info-strip">
          <GitBranch size={16} />
          <span>
            {draft.kind} gate
            {draft.x ? ` · ${draft.x} (${draft.x_transform.kind})` : ""}
            {draft.y ? ` × ${draft.y} (${draft.y_transform.kind})` : ""}
          </span>
        </div>
        {canMagnet && (
          <fieldset className="magnetic-gate-editor">
            <legend>
              <Magnet size={15} /> Population tracking
            </legend>
            <label className="check-field">
              <input
                type="checkbox"
                checked={!!draft.magnetic}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    magnetic: event.target.checked
                      ? { max_shift: 2 }
                      : undefined,
                  })
                }
              />
              Magnetic gate
            </label>
            <p className="muted">
              Translate this shape toward a nearby population for each sample
              and edited parent.
            </p>
            {draft.magnetic && (
              <>
                <label className="field">
                  Maximum magnetic movement (gate widths)
                  <input
                    type="number"
                    min="0"
                    max="4"
                    step="any"
                    required
                    value={draft.magnetic.max_shift}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        magnetic: { max_shift: Number(event.target.value) },
                      })
                    }
                  />
                </label>
                <button
                  type="button"
                  className="button small"
                  disabled={busy || magneticBusy || stale}
                  onClick={() => void previewMagnetic()}
                >
                  {magneticBusy
                    ? "Calculating position…"
                    : "Preview magnetic position"}
                </button>
                {preview && (
                  <div className="magnetic-preview" role="status">
                    <p>
                      Events inside shape:{" "}
                      {formatNumber(preview.magnetic.anchor_count, 0)} →{" "}
                      {formatNumber(preview.magnetic.resolved_count, 0)} of{" "}
                      {formatNumber(preview.magnetic.parent_count, 0)} parent
                      events.
                    </p>
                    <p>
                      Movement: {preview.magnetic.distance.toFixed(3)} gate
                      widths · shift{" "}
                      {preview.magnetic.shift
                        .map((v) => v.toPrecision(5))
                        .join(", ")}
                      .
                    </p>
                    {preview.magnetic.near_limit && (
                      <p className="notice">
                        Movement reached the search boundary. Review the
                        population and search distance.
                      </p>
                    )}
                    {preview.magnetic.status !== "resolved" && (
                      <p className="notice">
                        No eligible events were found near this gate. Its
                        original position is retained.
                      </p>
                    )}
                    <button
                      type="button"
                      className="button small"
                      disabled={busy || magneticBusy || stale}
                      onClick={() => {
                        setDraft(preview.gate);
                        setHoles(
                          (preview.gate.holes ?? []).map((ring) =>
                            ring.map((p) => p.join(", ")).join("\n"),
                          ),
                        );
                        setVertices(
                          preview.gate.vertices
                            .map((p) => p.join(", "))
                            .join("\n"),
                        );
                        setCoordinates(
                          preview.gate.coordinates?.join(", ") ?? "",
                        );
                        setCovariance(
                          preview.gate.covariance
                            ?.map((row) => row.join(", "))
                            .join("\n") ?? "",
                        );
                        setError("");
                      }}
                    >
                      Freeze reviewed position
                    </button>
                  </div>
                )}
                <p className="muted">
                  Movement uses a fixed count grid. Counts use every parent
                  event. The arrow marks the original and resolved positions.
                  Freeze a reviewed position to keep it as a static gate;
                  clearing the checkbox restores the original coordinates.
                </p>
              </>
            )}
          </fieldset>
        )}
        {draft.curly && (
          <fieldset className="gate-curly-settings">
            <legend>Shared curly geometry</legend>
            <div className="field-grid">
              {(["center", "coefficients"] as const).flatMap((key) =>
                [0, 1].map((axis) => (
                  <label className="field" key={`${key}${axis}`}>
                    {axis ? "Y" : "X"}{" "}
                    {key === "center" ? "curly center" : "noise coefficient"}
                    <input
                      type="number"
                      step="any"
                      required
                      min={key === "coefficients" ? 0 : undefined}
                      value={
                        Number.isFinite(draft.curly![key][axis])
                          ? draft.curly![key][axis]
                          : ""
                      }
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          curly: {
                            ...draft.curly!,
                            [key]: draft.curly![key].map((v, i) =>
                              i === axis
                                ? event.target.value === ""
                                  ? NaN
                                  : Number(event.target.value)
                                : v,
                            ) as [number, number],
                          },
                        })
                      }
                    />
                  </label>
                )),
              )}
            </div>
            <p className="form-note">
              The center uses the saved plot coordinates. Noise coefficients use
              raw fluorescence intensity units; review them for your instrument
              and controls. Initial coefficients are 1. Positive arms follow the
              square root of positive intensity; left and down arms stay
              straight.
            </p>
          </fieldset>
        )}
        {draft.spider && (
          <fieldset className="gate-spider-settings">
            <legend>Shared spider geometry</legend>
            <div className="field-grid">
              {(["center", "scale"] as const).flatMap((key) =>
                [0, 1].map((axis) => (
                  <label className="field" key={`${key}${axis}`}>
                    {axis ? "Y" : "X"} spider {key}
                    <input
                      type="number"
                      step="any"
                      required
                      value={
                        Number.isFinite(draft.spider![key][axis])
                          ? draft.spider![key][axis]
                          : ""
                      }
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          spider: {
                            ...draft.spider!,
                            [key]: draft.spider![key].map((v, i) =>
                              i === axis
                                ? event.target.value === ""
                                  ? NaN
                                  : Number(event.target.value)
                                : v,
                            ),
                          },
                        })
                      }
                    />
                  </label>
                )),
              )}
              {draft.spider.angles.map((value, arm) => (
                <label className="field" key={`angle${arm}`}>
                  Spider arm {arm + 1} angle (degrees)
                  <input
                    type="number"
                    step="any"
                    min={0}
                    max={359.999999}
                    required
                    value={
                      Number.isFinite(value) ? (value * 180) / Math.PI : ""
                    }
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        spider: {
                          ...draft.spider!,
                          angles: draft.spider!.angles.map((v, i) =>
                            i === arm
                              ? event.target.value === ""
                                ? NaN
                                : (Number(event.target.value) * Math.PI) / 180
                              : v,
                          ) as [number, number, number, number],
                        },
                      })
                    }
                  />
                </label>
              ))}
            </div>
            <p className="form-note">
              Angles turn counterclockwise in the saved X/Y scale. Arms start
              right, up, left and down. Coordinate scales are saved with the
              gate; resizing or zooming the plot does not change membership.
            </p>
          </fieldset>
        )}
        {draft.bounds.length > 0 && (
          <div className="field-grid">
            {draft.bounds.map((value, i) => (
              <label className="field" key={i}>
                {draft.kind === "quadrant"
                  ? `${i ? "Y" : "X"} threshold`
                  : ["X minimum", "X maximum", "Y minimum", "Y maximum"][i]}
                <input
                  type="number"
                  step="any"
                  required
                  value={Number.isFinite(value) ? value : ""}
                  onChange={(e) =>
                    updateBound(
                      i,
                      e.target.value === "" ? NaN : Number(e.target.value),
                    )
                  }
                />
              </label>
            ))}
          </div>
        )}
        {!!draft.dimensions?.length && (
          <div className="gate-dimensions">
            {draft.dimensions.map((dim, index) => (
              <fieldset key={index}>
                <legend>
                  Dimension {index + 1} · {dim.channel}
                </legend>
                <div className="field-row">
                  <label className="field grow">
                    Compensation
                    <select
                      value={dim.compensation_ref}
                      onChange={(event) =>
                        updateDimension(index, {
                          compensation_ref: event.target.value,
                        })
                      }
                    >
                      <option value="sample">
                        Current sample compensation
                      </option>
                      <option value="uncompensated">Uncompensated</option>
                      <option value="FCS">Embedded FCS matrix</option>
                      {workspace.compensations.map((matrix) => (
                        <option key={matrix.id} value={matrix.id}>
                          {matrix.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="field">
                    <span>Coordinate transform</span>
                    <strong>{dim.transform.kind}</strong>
                  </div>
                </div>
                {draft.kind === "hyperrectangle" && (
                  <div className="field-row">
                    {draft.partition ? (
                      <label className="field grow">
                        {index ? "Y" : "X"} threshold
                        <input
                          type="number"
                          step="any"
                          required
                          value={dim.minimum ?? dim.maximum ?? ""}
                          onChange={(event) =>
                            updateDimension(index, {
                              [dim.minimum !== null ? "minimum" : "maximum"]:
                                event.target.value === ""
                                  ? NaN
                                  : Number(event.target.value),
                            })
                          }
                        />
                      </label>
                    ) : (
                      (["minimum", "maximum"] as const).map((key) => (
                        <label className="field grow" key={key}>
                          {key === "minimum"
                            ? "Inclusive minimum"
                            : "Exclusive maximum"}
                          <input
                            type="number"
                            step="any"
                            value={dim[key] ?? ""}
                            placeholder={
                              key === "minimum" ? "−∞ (open)" : "+∞ (open)"
                            }
                            onChange={(event) =>
                              updateDimension(index, {
                                [key]:
                                  event.target.value === ""
                                    ? null
                                    : Number(event.target.value),
                              })
                            }
                          />
                        </label>
                      ))
                    )}
                  </div>
                )}
                <details>
                  <summary>Edit coordinate transform</summary>
                  <CoordinateTransform
                    value={dim.transform}
                    onChange={(transform) =>
                      updateDimension(index, { transform })
                    }
                  />
                </details>
                {dim.ratio_channels && (
                  <>
                    <p className="form-note">
                      Ratio: A × ({dim.ratio_channels[0]} − B) / (
                      {dim.ratio_channels[1]} − C)
                    </p>
                    <div className="field-grid">
                      {(["ratio_a", "ratio_b", "ratio_c"] as const).map(
                        (key) => (
                          <label className="field" key={key}>
                            {key.at(-1)?.toUpperCase()}
                            <input
                              type="number"
                              step="any"
                              value={dim[key]}
                              onChange={(event) =>
                                updateDimension(index, {
                                  [key]: Number(event.target.value),
                                })
                              }
                            />
                          </label>
                        ),
                      )}
                    </div>
                  </>
                )}
              </fieldset>
            ))}
          </div>
        )}
        {draft.kind === "ellipsoid" && (
          <>
            <label className="field">
              Center coordinates (comma separated)
              <input
                value={coordinates}
                onChange={(event) => setCoordinates(event.target.value)}
              />
            </label>
            <label className="field">
              Covariance matrix (one comma separated row per line)
              <textarea
                rows={Math.min(8, draft.dimensions?.length ?? 2)}
                className="mono"
                value={covariance}
                onChange={(event) => setCovariance(event.target.value)}
              />
            </label>
            <label className="field">
              Squared distance threshold
              <input
                type="number"
                min="0.0000001"
                step="any"
                value={draft.distance_square ?? 1}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    distance_square: Number(event.target.value),
                  })
                }
              />
            </label>
          </>
        )}
        {draft.kind === "ellipse" && (
          <div className="field-grid">
            {(["center", "radii"] as const).flatMap((key) =>
              (draft[key] ?? []).map((value, index) => (
                <label className="field" key={`${key}-${index}`}>
                  {`${index ? "Y" : "X"} ${key === "center" ? "center" : "radius"}`}
                  <input
                    type="number"
                    step="any"
                    required
                    value={Number.isFinite(value) ? value : ""}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        [key]: draft[key]!.map((v, i) =>
                          i === index
                            ? e.target.value === ""
                              ? NaN
                              : Number(e.target.value)
                            : v,
                        ),
                      })
                    }
                  />
                </label>
              )),
            )}
          </div>
        )}
        {draft.kind === "polygon" && (
          <>
            <label className="field">
              Vertices in transformed coordinates
              <textarea
                rows={5}
                value={vertices}
                onChange={(e) => setVertices(e.target.value)}
                spellCheck={false}
                className="mono"
              />
            </label>
            {holes.map((ring, r) => (
              <div key={r}>
                <label className="field">
                  Excluded ring {r + 1} in transformed coordinates
                  <textarea
                    aria-label={`Excluded ring ${r + 1} in transformed coordinates`}
                    rows={4}
                    value={ring}
                    onChange={(e) =>
                      setHoles(
                        holes.map((text, i) =>
                          i === r ? e.target.value : text,
                        ),
                      )
                    }
                    spellCheck={false}
                    className="mono"
                  />
                </label>
                <button
                  type="button"
                  className="button small"
                  onClick={() => setHoles(holes.filter((_, i) => i !== r))}
                >
                  Remove excluded ring {r + 1}
                </button>
              </div>
            ))}
            <button
              type="button"
              className="button small"
              disabled={holes.length >= 128}
              onClick={() => setHoles([...holes, ""])}
            >
              Add excluded ring
            </button>
            <p className="field-help">
              Excluded rings remove their interiors and boundaries. Overlapping
              excluded rings remain excluded.
            </p>
          </>
        )}
        {draft.kind === "boolean" && (
          <>
            <label className="field">
              Boolean operation
              <select
                value={draft.operation}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    operation: e.target.value as Gate["operation"],
                  })
                }
              >
                <option value="and">AND · Intersection</option>
                <option value="or">OR · Union</option>
                <option value="not">NOT · Complement within parent</option>
                <option value="xor">XOR · Exclusive union</option>
              </select>
            </label>
            <div className="checkbox-list">
              {gates.map((g) => (
                <label key={g.id}>
                  <input
                    type="checkbox"
                    checked={draft.operands.includes(g.id)}
                    onChange={(e) => {
                      const operands = e.target.checked
                        ? [...draft.operands, g.id]
                        : draft.operands.filter((v) => v !== g.id);
                      setDraft({
                        ...draft,
                        operands,
                        operand_complements: operands.map(
                          (value) =>
                            draft.operand_complements?.[
                              draft.operands.indexOf(value)
                            ] ?? false,
                        ),
                      });
                    }}
                  />
                  {g.name}
                </label>
              ))}
            </div>
            {draft.operands.map((operand, index) => (
              <label className="checkbox-field" key={operand}>
                <input
                  type="checkbox"
                  checked={draft.operand_complements?.[index] ?? false}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      operand_complements: draft.operands.map((_, i) =>
                        i === index
                          ? event.target.checked
                          : (draft.operand_complements?.[i] ?? false),
                      ),
                    })
                  }
                />
                Complement operand: {gates.find((g) => g.id === operand)?.name}
              </label>
            ))}
          </>
        )}
        {draft.kind === "quadrant" && (
          <p className="form-note">
            Creates four populations with shared thresholds. Boundary events
            belong to the upper or right quadrant.
          </p>
        )}
        {draft.kind === "quality" && (
          <div className="info-strip">
            <span>
              Reviewed acquisition events ·{" "}
              {draft.quality_keep === false ? "Rejected" : "Retained"}
              <br />
              {draft.quality_excluded_bins?.length ?? 0} excluded intervals.
              Revise interval and event exclusions in Acquisition QC.
            </span>
          </div>
        )}
        {draft.kind === "membership" && (
          <div className="info-strip">
            <span>
              Captured event identities ·{" "}
              {draft.membership?.selected_count.toLocaleString()} events. This
              selection remains fixed for its original acquisition. Capture a
              new snapshot to change it.
            </span>
          </div>
        )}
        {draft.kind !== "container" &&
          draft.kind !== "quality" &&
          !draft.partition && (
            <label className="checkbox-field">
              <input
                type="checkbox"
                checked={draft.complement ?? false}
                onChange={(event) =>
                  setDraft({ ...draft, complement: event.target.checked })
                }
              />
              Select the complement within the parent population
            </label>
          )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-footer">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button primary"
            disabled={busy || stale || shapeActive || !draft.name.trim()}
          >
            <Check size={16} />
            {existing ? "Save population" : "Create population"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function GroupDialog({
  workspace,
  selected,
  busy,
  commit,
  onClose,
}: {
  workspace: Workspace;
  selected: string[];
  busy: boolean;
  commit: Commit;
  onClose: () => void;
}) {
  const [name, setName] = useState("New group"),
    [sampleIds, setSampleIds] = useState(selected),
    [error, setError] = useState("");
  return (
    <Modal
      title="Create sample group"
      subtitle="Organize conditions, donors, plates, or any experimental cohort."
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            const group: Group = {
              id: id(),
              name: name.trim(),
              sample_ids: sampleIds,
              color: "#7ba8f8",
            };
            await commit("/groups", { group }, "Group created");
            onClose();
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <label className="field">
          Group name
          <input
            required
            maxLength={160}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <div className="checkbox-list">
          <label>
            <input
              type="checkbox"
              checked={
                sampleIds.length === workspace.samples.length &&
                sampleIds.length > 0
              }
              onChange={(e) =>
                setSampleIds(
                  e.target.checked ? workspace.samples.map((s) => s.id) : [],
                )
              }
            />
            Select all samples
          </label>
          {workspace.samples.map((s) => (
            <label key={s.id}>
              <input
                type="checkbox"
                checked={sampleIds.includes(s.id)}
                onChange={(e) =>
                  setSampleIds(
                    e.target.checked
                      ? [...sampleIds, s.id]
                      : sampleIds.filter((v) => v !== s.id),
                  )
                }
              />
              {s.name}
            </label>
          ))}
        </div>
        {error && <p className="form-error">{error}</p>}
        <div className="modal-footer">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button primary" disabled={busy || !name.trim()}>
            <Plus size={16} />
            Create group
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function SampleDialog({
  sample,
  commit,
  busy,
  onClose,
}: {
  sample: Sample;
  commit: Commit;
  busy: boolean;
  onClose: () => void;
}) {
  const [name, setName] = useState(sample.name),
    [tags, setTags] = useState(Object.entries(sample.tags)),
    [error, setError] = useState("");
  return (
    <Modal
      title="Sample properties"
      subtitle="Edit experiment metadata without modifying acquisition data."
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            if (
              tags.some(([k]) => !k.trim()) ||
              new Set(tags.map(([k]) => k.trim())).size !== tags.length
            )
              throw new Error("Metadata keys must be nonempty and unique");
            await commit(
              `/samples/${sample.id}`,
              {
                name,
                tags: Object.fromEntries(tags.map(([k, v]) => [k.trim(), v])),
              },
              "Sample updated",
              "PATCH",
            );
            onClose();
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <label className="field">
          Sample name
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <div className="section-label">Experiment metadata</div>
        {tags.map(([key, value], index) => (
          <div className="tag-edit-row" key={index}>
            <input
              aria-label={`Metadata key ${index + 1}`}
              placeholder="Key"
              value={key}
              onChange={(e) =>
                setTags(
                  tags.map((row, i) =>
                    i === index ? [e.target.value, row[1]] : row,
                  ),
                )
              }
            />
            <input
              aria-label={`Metadata value ${index + 1}`}
              placeholder="Value"
              value={value}
              onChange={(e) =>
                setTags(
                  tags.map((row, i) =>
                    i === index ? [row[0], e.target.value] : row,
                  ),
                )
              }
            />
            <button
              className="icon-button"
              type="button"
              aria-label="Remove metadata"
              onClick={() => setTags(tags.filter((_, i) => i !== index))}
            >
              <Trash2 size={16} />
            </button>
          </div>
        ))}
        <button
          type="button"
          className="text-button"
          onClick={() => setTags([...tags, ["", ""]])}
        >
          <Plus size={14} />
          Add metadata
        </button>
        {error && <p className="form-error">{error}</p>}
        <div className="modal-footer">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button disabled={busy} className="button primary">
            <Save size={16} />
            Save properties
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function ApplyDialog({
  workspace,
  source,
  targets,
  commit,
  busy,
  onClose,
}: {
  workspace: Workspace;
  source: Sample;
  targets: string[];
  commit: Commit;
  busy: boolean;
  onClose: () => void;
}) {
  const [sampleIds, setSampleIds] = useState(
      targets.filter((s) => s !== source.id),
    ),
    [replace, setReplace] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal
      title="Apply gating tree"
      subtitle={`Copy all populations from ${source.name} to compatible samples.`}
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await commit(
              "/gates/apply",
              {
                source_sample_id: source.id,
                target_sample_ids: sampleIds,
                replace,
              },
              "Gating tree applied",
            );
            onClose();
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <div className="info-strip">
          <GitBranch size={17} />
          <span>
            {workspace.gates.filter((g) => g.sample_id === source.id).length}{" "}
            populations
          </span>
          <ArrowRight size={15} />
          <span>{sampleIds.length} samples</span>
        </div>
        <div className="checkbox-list">
          {workspace.samples
            .filter((s) => s.id !== source.id)
            .map((s) => (
              <label key={s.id}>
                <input
                  type="checkbox"
                  checked={sampleIds.includes(s.id)}
                  onChange={(e) =>
                    setSampleIds(
                      e.target.checked
                        ? [...sampleIds, s.id]
                        : sampleIds.filter((v) => v !== s.id),
                    )
                  }
                />
                {s.name}
              </label>
            ))}
        </div>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={replace}
            onChange={(e) => setReplace(e.target.checked)}
          />
          Replace existing gating trees in target samples
        </label>
        <p className="form-note">
          Incompatible channel panels are rejected before any sample is changed.
          You can undo this operation.
        </p>
        {error && <p className="form-error">{error}</p>}
        <div className="modal-footer">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button primary"
            disabled={busy || !sampleIds.length}
          >
            <GitBranch size={16} />
            Apply to {sampleIds.length} samples
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function CompensationPanel({
  workspace,
  sample,
  commit,
  busy,
}: {
  workspace: Workspace;
  sample: Sample | null;
  commit: Commit;
  busy: boolean;
}) {
  const [draft, setDraft] = useState<Compensation | null>(
    workspace.compensations[0] ?? null,
  );
  const [selected, setSelected] = useState<string[]>(sample ? [sample.id] : []);
  const [dirty, setDirty] = useState(false),
    [error, setError] = useState("");
  const [wizard, setWizard] = useState(false);
  const [spreading, setSpreading] = useState(false);
  const newMatrix = () => {
    const detectors =
      (sample ? acquisitionChannels(sample) : [])
        .filter(
          (c) =>
            !c.name.toLowerCase().startsWith("fsc") &&
            !c.name.toLowerCase().startsWith("ssc") &&
            c.name.toLowerCase() !== "time",
        )
        .map((c) => c.name) ?? [];
    if (!detectors.length) return;
    setDraft({
      id: id(),
      name: "New compensation",
      detectors,
      outputs: detectors,
      matrix: detectors.map((_, i) =>
        detectors.map((__, j) => Number(i === j)),
      ),
      kind: "spillover",
      source: "Manual",
      background: [],
      weights: [],
      provenance: {},
    });
    setDirty(true);
  };
  const save = async () => {
    if (!draft) return;
    try {
      if (!draft.matrix.flat().every(Number.isFinite))
        throw new Error("All matrix cells must contain a finite number");
      if (
        !(draft.background ?? []).every(Number.isFinite) ||
        !(draft.weights ?? []).every((w) => Number.isFinite(w) && w > 0)
      )
        throw new Error(
          "Background must be finite and detector weights must be positive",
        );
      const state = await commit(
        "/compensations",
        { compensation: draft, sample_ids: selected },
        "Compensation saved and applied",
      );
      setDraft(state.compensations.find((c) => c.id === draft.id)!);
      setDirty(false);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <div className="page-panel">
      <div className="page-heading">
        <div>
          <span className="eyebrow">SPECTRAL CORRECTION</span>
          <h1>Compensation</h1>
          <p>Inspect and refine detector spillover across your experiment.</p>
        </div>
        <div className="heading-actions">
          <button
            className="button primary"
            onClick={() => setWizard(true)}
            disabled={!sample || busy}
          >
            <FlaskConical size={16} />
            Control wizard
          </button>
          <button
            className="button"
            disabled={
              !draft ||
              dirty ||
              busy ||
              !workspace.compensations.some((c) => c.id === draft.id)
            }
            title={
              dirty
                ? "Save matrix edits before reviewing spreading"
                : "Review spillover spreading"
            }
            onClick={() => setSpreading(true)}
          >
            Spillover spreading
          </button>
          <button
            className="button"
            onClick={newMatrix}
            disabled={!sample || busy}
          >
            <Plus size={16} />
            New matrix
          </button>
        </div>
      </div>
      <div className="matrix-layout">
        <section className="panel matrix-panel">
          <div className="panel-heading">
            <select
              aria-label="Compensation matrix"
              value={draft?.id ?? ""}
              onChange={(e) => {
                setDraft(
                  workspace.compensations.find(
                    (c) => c.id === e.target.value,
                  ) ?? null,
                );
                setDirty(false);
              }}
            >
              <option value="" disabled>
                Select matrix
              </option>
              {workspace.compensations.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
              {draft &&
                !workspace.compensations.some((c) => c.id === draft.id) && (
                  <option value={draft.id}>{draft.name}</option>
                )}
            </select>
            <Tag>{draft?.kind === "spectral" ? "Spectral" : "Spillover"}</Tag>
          </div>
          {draft ? (
            <>
              <div className="matrix-name">
                <label className="field">
                  Matrix name
                  <input
                    value={draft.name}
                    maxLength={160}
                    onChange={(e) => {
                      setDraft({ ...draft, name: e.target.value });
                      setDirty(true);
                    }}
                  />
                </label>
                <span className="muted">{draft.source}</span>
              </div>
              <div className="table-scroll">
                <table className="matrix-table">
                  <thead>
                    <tr>
                      <th>Source ↓ / Detector →</th>
                      {draft.detectors.map((name) => (
                        <th key={name}>{name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {draft.matrix.map((row, i) => (
                      <tr key={i}>
                        <th>{draft.outputs[i]}</th>
                        {row.map((value, j) => (
                          <td
                            key={j}
                            style={{
                              backgroundColor:
                                i === j
                                  ? "#38d9ba0c"
                                  : `rgba(123,168,248,${Math.min(Math.abs(value), 0.7) * 0.6})`,
                            }}
                          >
                            <input
                              aria-label={`${draft.outputs[i]} to ${draft.detectors[j]} percent`}
                              type="number"
                              step="0.01"
                              value={
                                Number.isFinite(value)
                                  ? Number((value * 100).toFixed(5))
                                  : ""
                              }
                              onChange={(e) => {
                                const next = draft.matrix.map((r) => [...r]);
                                next[i][j] =
                                  e.target.value === ""
                                    ? NaN
                                    : Number(e.target.value) / 100;
                                setDraft({ ...draft, matrix: next });
                                setDirty(true);
                              }}
                            />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="matrix-note">
                <span>
                  Values shown as percentages. Rows are source fluorochromes;
                  columns are measured detectors.
                </span>
                {draft.kind === "spillover" && (
                  <button
                    className="text-button"
                    onClick={() => {
                      setDraft({
                        ...draft,
                        matrix: draft.outputs.map((_, i) =>
                          draft.detectors.map((__, j) => Number(i === j)),
                        ),
                      });
                      setDirty(true);
                    }}
                  >
                    Reset to identity
                  </button>
                )}
              </div>
              {draft.kind === "spectral" && (
                <div className="spectral-matrix-options">
                  <p className="form-note">
                    Outputs use the reference matrix's intensity units.
                    Background is subtracted from acquired detector values;
                    weights are inverse detector variances.
                  </p>
                  <div className="table-scroll">
                    <table className="diagnostic-table">
                      <thead>
                        <tr>
                          <th>Detector</th>
                          <th>Background</th>
                          <th>Weight</th>
                        </tr>
                      </thead>
                      <tbody>
                        {draft.detectors.map((name, i) => (
                          <tr key={name}>
                            <th>{name}</th>
                            <td>
                              <input
                                aria-label={`${name} spectral background`}
                                type="number"
                                step="any"
                                value={
                                  Number.isFinite(draft.background?.[i] ?? 0)
                                    ? (draft.background?.[i] ?? 0)
                                    : ""
                                }
                                onChange={(e) => {
                                  const values = draft.detectors.map(
                                    (_, j) => draft.background?.[j] ?? 0,
                                  );
                                  values[i] =
                                    e.target.value === ""
                                      ? NaN
                                      : Number(e.target.value);
                                  setDraft({ ...draft, background: values });
                                  setDirty(true);
                                }}
                              />
                            </td>
                            <td>
                              <input
                                aria-label={`${name} spectral weight`}
                                type="number"
                                min="0"
                                step="any"
                                value={
                                  Number.isFinite(draft.weights?.[i] ?? 1)
                                    ? (draft.weights?.[i] ?? 1)
                                    : ""
                                }
                                onChange={(e) => {
                                  const values = draft.detectors.map(
                                    (_, j) => draft.weights?.[j] ?? 1,
                                  );
                                  values[i] =
                                    e.target.value === ""
                                      ? NaN
                                      : Number(e.target.value);
                                  setDraft({ ...draft, weights: values });
                                  setDirty(true);
                                }}
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {draft.provenance?.kind === "control_calculation" && (
                <p className="matrix-provenance form-note">
                  Calculated from single-stain control medians. Raw event
                  hashes, population definitions, original coefficients and
                  diagnostics are preserved in the project archive.
                </p>
              )}
            </>
          ) : (
            <div className="empty-state compact">
              <h2>No matrices yet</h2>
              <p>
                Import an FCS acquisition matrix or create one for your panel.
              </p>
              <button
                className="button primary"
                onClick={newMatrix}
                disabled={!sample}
              >
                Create matrix
              </button>
            </div>
          )}
        </section>
        <aside className="panel matrix-controls">
          <div className="panel-heading">
            <h3>Apply to samples</h3>
            <Tag>{selected.length}</Tag>
          </div>
          <div className="checkbox-list">
            <label>
              <input
                type="checkbox"
                checked={
                  selected.length === workspace.samples.length &&
                  selected.length > 0
                }
                onChange={(e) =>
                  setSelected(
                    e.target.checked ? workspace.samples.map((s) => s.id) : [],
                  )
                }
              />
              All samples
            </label>
            {workspace.samples.map((s) => (
              <label key={s.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(s.id)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, s.id]
                        : selected.filter((v) => v !== s.id),
                    )
                  }
                />
                <span>{s.name}</span>
              </label>
            ))}
          </div>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button
            className="button primary full-width"
            disabled={!draft || busy || !draft.name.trim()}
            onClick={save}
          >
            <Check size={16} />
            {dirty ? "Save & apply matrix" : "Apply matrix"}
          </button>
          <button
            className="button full-width"
            disabled={!selected.length || busy}
            onClick={() =>
              commit(
                "/compensations/assign",
                { compensation_id: null, sample_ids: selected },
                "Compensation removed from selected samples",
              ).catch((err) => setError(err.message))
            }
          >
            Use uncompensated data
          </button>
          <p className="form-note">
            Gate counts and statistics update after compensation changes.
            Invalid or unstable matrices are rejected.
          </p>
          {workspace.samples.some((s) =>
            s.unmixed_parameters?.some(
              (n) =>
                !workspace.compensations
                  .find((c) => c.id === s.compensation_id)
                  ?.outputs.includes(n),
            ),
          ) && (
            <p className="form-note">
              Some spectral output parameters are inactive. Assign a matrix
              containing those outputs to calculate their values; inactive
              outputs are undefined.
            </p>
          )}
        </aside>
      </div>
      {wizard && (
        <CompensationWizard
          workspace={workspace}
          selected={selected}
          commit={commit}
          onClose={() => setWizard(false)}
          onSaved={(matrix) => {
            setDraft(matrix);
            setDirty(false);
            setError("");
          }}
        />
      )}
      {spreading && draft && (
        <AutoSpreadDialog
          key={draft.id}
          workspace={workspace}
          matrix={draft}
          commit={commit}
          onClose={() => setSpreading(false)}
          onSaved={(matrix) => {
            setDraft(matrix);
            setDirty(false);
            setError("");
          }}
        />
      )}
    </div>
  );
}

export function TransformDialog({
  workspace,
  sample,
  channelName,
  commit,
  busy,
  onClose,
}: {
  workspace: Workspace;
  sample: Sample;
  channelName: string;
  commit: Commit;
  busy: boolean;
  onClose: () => void;
}) {
  const channel = sample.channels.find((c) => c.name === channelName)!;
  const [spec, setSpec] = useState(channel.transform),
    [all, setAll] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal
      title="Channel transformation"
      subtitle={channelLabel(channel)}
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await commit(
              "/transforms",
              {
                channel: channelName,
                transform: spec,
                sample_ids: all
                  ? workspace.samples.map((s) => s.id)
                  : [sample.id],
              },
              "Display transform updated",
            );
            onClose();
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <CoordinateTransform value={spec} onChange={setSpec} />
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={all}
            onChange={(e) => setAll(e.target.checked)}
          />
          Apply to this channel in all samples
        </label>
        <p className="form-note">
          Existing gates retain the coordinate transform used when they were
          drawn. Display changes preserve population membership.
        </p>
        {error && <p className="form-error">{error}</p>}
        <div className="modal-footer">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button primary" disabled={busy}>
            <Check size={16} />
            Apply transformation
          </button>
        </div>
      </form>
    </Modal>
  );
}
