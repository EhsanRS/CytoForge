import { useEffect, useRef, useState } from "react";
import { Check, Settings2, X } from "lucide-react";
import type { Gate, Workspace } from "../types";
import { gateShape } from "../gateGeometry";
import { GateShapeEditor } from "./GateShapeEditor";

export function canEditGateOnPlot(gate: Gate | null | undefined): gate is Gate {
  if (!gate) return false;
  try {
    gateShape(gate);
    return true;
  } catch {
    return false;
  }
}

export function InlineGateEditor({
  gate,
  workspace,
  baseRevision,
  busy,
  onSave,
  onClose,
  onRebase,
  onDetails,
  pooledScope,
}: {
  gate: Gate;
  workspace: Workspace;
  baseRevision: number | null;
  busy: boolean;
  onSave: (value: Gate) => Promise<unknown>;
  onClose: () => void;
  onRebase: () => void;
  onDetails: (value: Gate) => void;
  pooledScope?: import("../types").PooledScope;
}) {
  const [draft, setDraft] = useState(gate);
  const [active, setActive] = useState(false);
  const [error, setError] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  const current = workspace.gates.find((g) => g.id === gate.id);
  const stale = baseRevision !== workspace.revision;
  const missing =
    !current || !workspace.samples.some((s) => s.id === gate.sample_id);
  const save = async () => {
    if (busy || stale || active || missing) return;
    setError("");
    try {
      await onSave(draft);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <section
      className="inline-gate-editor"
      aria-label="Edit gate on plot"
      data-gate-id={gate.id}
    >
      <div className="inline-gate-heading">
        <div>
          <h2 ref={heading} tabIndex={-1}>
            Editing {draft.name}
          </h2>
          <p>
            The plot shows the parent population in this gate's saved
            coordinates.
          </p>
        </div>
        <div className="button-row">
          <button
            className="button small"
            disabled={busy || active}
            onClick={() => onDetails(draft)}
          >
            <Settings2 size={14} />
            Numeric settings
          </button>
          <button className="button small" disabled={busy} onClick={onClose}>
            <X size={14} />
            Cancel gate edit
          </button>
          <button
            className="button small primary"
            disabled={busy || stale || active || missing}
            onClick={() => void save()}
          >
            <Check size={14} />
            Apply gate edit
          </button>
        </div>
      </div>
      {stale && (
        <div className="gate-draft-conflict" role="alert">
          <strong>
            The workspace changed while this gate was being edited.
          </strong>
          <p>
            Your draft is retained. Review the saved population and its
            coordinate definitions before applying it.
          </p>
          {current ? (
            <details>
              <summary>Current saved population: {current.name}</summary>
              <pre>{JSON.stringify(current, null, 2)}</pre>
            </details>
          ) : (
            <p>
              This population was removed. Restore it before applying this
              draft, or cancel the edit.
            </p>
          )}
          {!missing && (
            <button
              className="button small"
              disabled={busy || active}
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
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <GateShapeEditor
        gate={draft}
        workspace={workspace}
        disabled={busy || stale || missing}
        onChange={setDraft}
        onActiveChange={setActive}
        pooledScope={pooledScope}
      />
    </section>
  );
}
