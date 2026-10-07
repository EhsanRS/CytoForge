import { useState } from "react";
import { Camera, LoaderCircle } from "lucide-react";
import { formatNumber, type Gate, type Sample } from "../types";
import { Modal } from "./Common";
import type { Commit } from "./Editors";

export function PopulationSnapshotDialog({
  sample,
  gate,
  commit,
  onClose,
  onSaved,
}: {
  sample: Sample;
  gate: Gate | null;
  commit: Commit;
  onClose: () => void;
  onSaved: (id: string) => void;
}) {
  const [name, setName] = useState(
    `${gate?.name ?? sample.name} snapshot`.slice(0, 160),
  );
  const [compensated, setCompensated] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setWorking(true);
    setError("");
    try {
      const document = await commit(
        "/gates/capture",
        {
          sample_id: sample.id,
          gate_id: gate?.id ?? null,
          name: name.trim(),
          compensated,
        },
        "Population snapshot saved",
      );
      const added = document.gates.at(-1);
      if (!added?.membership)
        throw new Error("The saved population was not returned");
      onSaved(added.id);
      onClose();
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value));
    } finally {
      setWorking(false);
    }
  };
  return (
    <Modal
      title="Capture population"
      subtitle={sample.name}
      onClose={() => {
        if (!working) onClose();
      }}
    >
      <p>
        Save the selected event identities as a reusable population. Later
        changes to gate geometry, analysis parameters or compensation preserve
        this selection. The snapshot can be used as a compensation or AF
        control.
      </p>
      <label className="field">
        Population name
        <input
          maxLength={160}
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={working}
        />
      </label>
      <label className="field">
        Selection coordinates
        <select
          value={compensated ? "current" : "acquired"}
          disabled={working}
          onChange={(event) => setCompensated(event.target.value === "current")}
        >
          <option value="current">Current population coordinates</option>
          <option value="acquired">Acquired detector coordinates</option>
        </select>
      </label>
      <p className="form-note">
        {gate?.name ?? "All acquired events"} ·{" "}
        {formatNumber(sample.event_count, 0)} acquired events. Captured
        populations retain this acquisition and cannot be copied as geometry to
        another sample.
      </p>
      {error && <div className="error-strip">{error}</div>}
      <div className="modal-footer">
        <button className="button" disabled={working} onClick={onClose}>
          Cancel
        </button>
        <button
          className="button primary"
          disabled={working || !name.trim()}
          onClick={() => void save()}
        >
          {working ? (
            <LoaderCircle size={16} className="spin" />
          ) : (
            <Camera size={16} />
          )}
          Save snapshot
        </button>
      </div>
    </Modal>
  );
}
