import { useState } from "react";
import { post } from "../api";
import {
  acquisitionChannels,
  channelLabel,
  linear,
  type GateDimension,
  type Sample,
  type Workspace,
} from "../types";
import { Modal } from "./Common";
import { CoordinateTransform } from "./Editors";

export function AxisDefinitionDialog({
  axis,
  workspace,
  sample,
  value,
  onApply,
  onClose,
  pooledScope,
}: {
  axis: string;
  workspace: Workspace;
  sample: Sample;
  value: GateDimension;
  onApply: (dimension: GateDimension) => void;
  onClose: () => void;
  pooledScope?: import("../types").PooledScope;
}) {
  const [draft, setDraft] = useState<GateDimension>({
    ...value,
    minimum: null,
    maximum: null,
  });
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const patch = (values: Partial<GateDimension>) =>
    setDraft((d) => ({ ...d, ...values }));
  const measured = new Set(acquisitionChannels(sample).map((c) => c.name));
  const matrices = workspace.compensations.filter((m) =>
    m.detectors.every((d) => measured.has(d)),
  );
  return (
    <Modal
      title={`${axis} coordinate definition`}
      subtitle="This plot window"
      onClose={busy ? () => {} : onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            const dimension = await post<GateDimension>(
              `/workspaces/${workspace.id}/samples/${sample.id}/coordinates/validate`,
              {
                revision: workspace.revision,
                dimension: draft,
                ...(pooledScope ? { scope: pooledScope } : {}),
              },
            );
            onApply(dimension);
            onClose();
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <fieldset disabled={busy} className="axis-definition-fields">
          <label className="field">
            Parameter type
            <select
              aria-label="Parameter type"
              value={draft.ratio_channels ? "ratio" : "channel"}
              onChange={(e) => {
                const first = sample.channels[0].name,
                  second = sample.channels[1]?.name ?? first;
                patch(
                  e.target.value === "ratio"
                    ? {
                        channel: `${first} / ${second}`,
                        ratio_channels: [first, second],
                        transform: linear,
                      }
                    : {
                        channel: first,
                        ratio_channels: null,
                        transform: sample.channels[0].transform,
                        ratio_a: 1,
                        ratio_b: 0,
                        ratio_c: 0,
                        ratio_bound_min: null,
                        ratio_bound_max: null,
                      },
                );
              }}
            >
              <option value="channel">Channel</option>
              <option value="ratio">Ratio</option>
            </select>
          </label>
          {!draft.ratio_channels ? (
            <label className="field">
              Parameter
              <select
                aria-label="Parameter"
                value={draft.channel}
                onChange={(e) => {
                  const channel = sample.channels.find(
                    (c) => c.name === e.target.value,
                  )!;
                  patch({
                    channel: channel.name,
                    transform: channel.transform,
                  });
                }}
              >
                {sample.channels.map((c) => (
                  <option key={c.name} value={c.name}>
                    {channelLabel(c)}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              <label className="field">
                Ratio label
                <input
                  value={draft.channel}
                  required
                  maxLength={160}
                  onChange={(e) => patch({ channel: e.target.value })}
                />
              </label>
              <div className="field-row">
                {(["Numerator", "Denominator"] as const).map((name, i) => (
                  <label className="field grow" key={name}>
                    {name}
                    <select
                      aria-label={name}
                      value={draft.ratio_channels![i]}
                      onChange={(e) =>
                        patch({
                          ratio_channels: draft.ratio_channels!.map((v, n) =>
                            n === i ? e.target.value : v,
                          ) as [string, string],
                        })
                      }
                    >
                      {sample.channels.map((c) => (
                        <option key={c.name} value={c.name}>
                          {channelLabel(c)}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
              <p className="form-note">
                A × (numerator − B) / (denominator − C)
              </p>
              <div className="field-grid">
                {(["ratio_a", "ratio_b", "ratio_c"] as const).map((key) => (
                  <label className="field" key={key}>
                    {key.at(-1)!.toUpperCase()}
                    <input
                      type="number"
                      step="any"
                      required
                      value={Number.isFinite(draft[key]) ? draft[key] : ""}
                      onChange={(e) =>
                        patch({
                          [key]:
                            e.target.value === ""
                              ? NaN
                              : Number(e.target.value),
                        })
                      }
                    />
                  </label>
                ))}
              </div>
              <div className="field-row">
                {(["ratio_bound_min", "ratio_bound_max"] as const).map(
                  (key) => (
                    <label className="field grow" key={key}>
                      {key === "ratio_bound_min"
                        ? "Ratio clamp minimum"
                        : "Ratio clamp maximum"}
                      <input
                        type="number"
                        step="any"
                        placeholder="Unbounded"
                        value={draft[key] ?? ""}
                        onChange={(e) =>
                          patch({
                            [key]:
                              e.target.value === ""
                                ? null
                                : Number(e.target.value),
                          })
                        }
                      />
                    </label>
                  ),
                )}
              </div>
            </>
          )}
          <label className="field">
            Compensation
            <select
              aria-label="Compensation"
              value={draft.compensation_ref}
              onChange={(e) => patch({ compensation_ref: e.target.value })}
            >
              <option value="sample">Current sample compensation</option>
              <option value="uncompensated">Raw / uncompensated</option>
              <option value="FCS">Embedded FCS matrix (if available)</option>
              {matrices.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
              {![
                "sample",
                "uncompensated",
                "FCS",
                ...matrices.map((m) => m.id),
              ].includes(draft.compensation_ref) && (
                <option value={draft.compensation_ref}>
                  Unavailable matrix
                </option>
              )}
            </select>
          </label>
          <CoordinateTransform
            value={draft.transform}
            onChange={(transform) => patch({ transform })}
          />
        </fieldset>
        <p className="form-note">
          New gates retain this definition. Ratios use the chosen compensation
          for both inputs; nonfinite results are excluded from the plot.
        </p>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
          <button className="button primary" disabled={busy}>
            {busy ? "Checking…" : "Apply to plot"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
