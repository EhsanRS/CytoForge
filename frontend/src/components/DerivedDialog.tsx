import { useState } from "react";
import { Braces, Check } from "lucide-react";
import type { Sample, Workspace } from "../types";
import { channelLabel, linear } from "../types";
import type { Commit } from "./Editors";
import { Modal } from "./Common";

export function DerivedDialog({
  workspace,
  sample,
  commit,
  busy,
  onClose,
}: {
  workspace: Workspace;
  sample: Sample;
  commit: Commit;
  busy: boolean;
  onClose: () => void;
}) {
  const [name, setName] = useState("Ratio"),
    [label, setLabel] = useState(""),
    [expression, setExpression] = useState(
      `ch(${JSON.stringify(sample.channels[0].name)}) / max(ch(${JSON.stringify(sample.channels[1]?.name ?? sample.channels[0].name)}), 1)`,
    ),
    [all, setAll] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal
      title="Derived parameter"
      subtitle="Create an event-level parameter from existing acquisition or derived channels."
      onClose={onClose}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            await commit(
              "/derived",
              {
                sample_ids: all
                  ? workspace.samples.map((s) => s.id)
                  : [sample.id],
                parameter: {
                  name: name.trim(),
                  label,
                  expression,
                  transform:
                    sample.channels.find((c) => c.name === name)?.transform ??
                    linear,
                },
              },
              "Derived parameter saved",
            );
            onClose();
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <label className="field">
          Parameter name
          <input
            required
            maxLength={160}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="field">
          Display label
          <input value={label} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label className="field">
          Expression
          <textarea
            aria-label="Expression"
            className="mono"
            rows={3}
            required
            maxLength={1024}
            spellCheck={false}
            value={expression}
            onChange={(e) => setExpression(e.target.value)}
          />
        </label>
        <div className="formula-channels">
          <span className="section-label">INSERT CHANNEL REFERENCE</span>
          <div>
            {sample.channels.map((c) => (
              <button
                type="button"
                className="button small"
                key={c.name}
                title={channelLabel(c)}
                onClick={() =>
                  setExpression(expression + `ch(${JSON.stringify(c.name)})`)
                }
              >
                <Braces size={11} />
                {c.name}
              </button>
            ))}
          </div>
        </div>
        <p className="form-note">
          Use +, −, *, /, ** and abs, sqrt, log, log10, exp, asinh, min, max, or
          clip. Inputs use compensated intensities. Division by zero and other
          undefined values are retained as missing values.
        </p>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={all}
            onChange={(e) => setAll(e.target.checked)}
          />
          Apply to all samples in this workspace
        </label>
        {!!sample.derived_parameters?.length && (
          <label className="field">
            Load existing definition
            <select
              defaultValue=""
              onChange={(e) => {
                const d = sample.derived_parameters.find(
                  (d) => d.name === e.target.value,
                );
                if (d) {
                  setName(d.name);
                  setLabel(d.label);
                  setExpression(d.expression);
                }
              }}
            >
              <option value="" disabled>
                Select a derived parameter
              </option>
              {sample.derived_parameters.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-footer">
          <button className="button" type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button primary" disabled={busy || !name.trim()}>
            <Check size={16} />
            Save parameter
          </button>
        </div>
      </form>
    </Modal>
  );
}
