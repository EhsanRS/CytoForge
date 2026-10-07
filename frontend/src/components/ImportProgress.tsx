import { LoaderCircle } from "lucide-react";
import type { ImportSession } from "../types";
import { formatNumber } from "../types";
import { Modal } from "./Common";

export function ImportProgress({
  session,
  cancel,
}: {
  session: ImportSession;
  cancel: () => void;
}) {
  const saving = session.status === "committing";
  const transferring = session.status === "ready";
  const stopping = session.cancel_requested;
  const receiving =
    session.status === "ready" || session.status === "receiving";
  const maximum = receiving ? session.bytes_total : session.event_total;
  const value = receiving ? session.bytes_received : session.events_read;
  return (
    <Modal
      title={stopping ? "Stopping import" : "Importing samples"}
      subtitle={
        session.status === "ready" ? "Transferring files" : session.stage
      }
      onClose={cancel}
    >
      <div className="sample-import-progress" role="status" aria-live="polite">
        <div className="sample-import-file">
          <LoaderCircle size={20} className="spin" />
          <div>
            <strong>{session.file || "Selected acquisitions"}</strong>
            <p>
              {session.file_index > 0 &&
                "File " + session.file_index + " of " + session.file_count}
              {session.dataset_index > 0 &&
                (session.file_index > 0 ? " · " : "") +
                  "Dataset " +
                  session.dataset_index}
            </p>
          </div>
        </div>
        <progress
          aria-label="Sample import progress"
          max={Math.max(1, maximum)}
          value={
            maximum && !saving && !stopping && !transferring
              ? Math.min(value, maximum)
              : undefined
          }
        />
        <div className="sample-import-counts">
          <span>
            {transferring
              ? "Transferring " +
                formatNumber(session.bytes_total / 1024 ** 2, 1) +
                " MiB"
              : receiving
                ? formatNumber(session.bytes_received / 1024 ** 2, 1) +
                  " MiB received"
                : formatNumber(session.events_read, 0) +
                  (session.event_total
                    ? " of " + formatNumber(session.event_total, 0)
                    : "") +
                  " events"}
          </span>
          <span>
            {formatNumber(session.samples_prepared || 0, 0)} samples prepared
          </span>
        </div>
        <p className="form-note">
          {saving
            ? "Saving this batch to the experiment. You can undo the import after it finishes."
            : "Cancelling discards the entire import batch. Failed files are reported when it finishes."}
        </p>
      </div>
      <div className="modal-footer">
        <button
          className="button"
          onClick={cancel}
          disabled={saving || stopping}
        >
          {stopping ? "Stopping…" : "Cancel import"}
        </button>
      </div>
    </Modal>
  );
}
