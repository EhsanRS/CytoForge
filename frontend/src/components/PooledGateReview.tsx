import { useEffect, useState } from "react";
import { api, post } from "../api";
import type { Gate, PooledScope, Workspace } from "../types";
import { formatNumber } from "../types";
import { Modal } from "./Common";

export interface PooledGateProposal {
  scope: PooledScope;
  revision: number;
  action: "create" | "edit" | "delete";
  gates: Gate[];
  resolve: () => void;
  reject: (error: Error) => void;
}
interface Review {
  revision: number;
  review_hash: string;
  group_name: string;
  changed: boolean;
  population_count: number;
  samples: {
    sample_id: string;
    sample_name: string;
    compensation_id: string | null;
    populations: {
      id: string;
      name: string;
      count: number | null;
      parent_count: number | null;
      error?: string;
    }[];
  }[];
  affected_models: { id: string; name: string }[];
}

export function PooledGateReview({
  workspace,
  proposal,
  onApplied,
  onClose,
}: {
  workspace: Workspace;
  proposal: PooledGateProposal;
  onApplied: (doc: Workspace) => void;
  onClose: () => void;
}) {
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [externalRevision, setExternalRevision] = useState(proposal.revision);
  const stale =
    workspace.revision !== proposal.revision ||
    externalRevision !== proposal.revision;
  const prefix = `/workspaces/${workspace.id}/virtual-groups/gates`;
  const request = {
    ...proposal.scope,
    revision: proposal.revision,
    action: proposal.action,
    gates: proposal.gates,
  };
  useEffect(() => {
    const controller = new AbortController();
    api<Review>(`${prefix}/preview`, {
      method: "POST",
      body: JSON.stringify(request),
      signal: controller.signal,
    })
      .then(setReview)
      .catch((err: Error) => {
        if (!controller.signal.aborted) setError(err.message);
      });
    return () => controller.abort();
  }, [prefix, proposal]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setInterval(() => {
      api<{ revision: number }>(`/workspaces/${workspace.id}/revision`, {
        signal: controller.signal,
      })
        .then((r) => setExternalRevision(r.revision))
        .catch((err: Error) => {
          if (!controller.signal.aborted) setError(err.message);
        });
    }, 600);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [workspace.id]);
  const close = () => {
    if (busy) return;
    proposal.reject(
      new Error(
        proposal.action === "delete"
          ? "Group removal canceled. Populations are unchanged."
          : "Group gate review canceled. The draft is still available.",
      ),
    );
    onClose();
  };
  const apply = async () => {
    if (!review || stale || busy || !review.changed) return;
    setBusy(true);
    setError("");
    try {
      const saved = await post<Workspace>(`${prefix}/apply`, {
        ...request,
        review_hash: review.review_hash,
      });
      onApplied(saved);
      proposal.resolve();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Apply gates to pooled group"
      subtitle={review?.group_name ?? "Review all original samples"}
      onClose={close}
    >
      <p>
        Each sample keeps its own events and correction.{" "}
        {proposal.action === "delete"
          ? "These populations and their dependent branches will be removed from every member below."
          : "These gates will be applied to every member below."}{" "}
        The whole change can be undone once.
      </p>
      {proposal.gates.some((gate) => gate.magnetic) && (
        <p>
          Magnetic gates follow each sample's local population. Resolved
          positions may differ between samples.
        </p>
      )}
      {!review && !error && <p role="status">Calculating population counts…</p>}
      {review && (
        <div className="pooled-gate-review" aria-label="Pooled gate counts">
          <table>
            <thead>
              <tr>
                <th>Sample</th>
                <th>Population</th>
                <th>Events</th>
                <th>Parent events</th>
              </tr>
            </thead>
            <tbody>
              {review.samples.flatMap((sample) =>
                sample.populations.map((gate) => (
                  <tr key={gate.id}>
                    <td>{sample.sample_name}</td>
                    <td title={gate.error}>
                      {gate.name}
                      {gate.error && ` · ${gate.error}`}
                    </td>
                    <td>{formatNumber(gate.count, 0)}</td>
                    <td>{formatNumber(gate.parent_count, 0)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
          {!!review.affected_models.length && (
            <p className="form-error">
              These fitted models will need to be refit:{" "}
              {review.affected_models.map((m) => m.name).join(", ")}.
            </p>
          )}
        </div>
      )}
      {stale && (
        <p className="form-error" role="alert">
          Workspace changed. Cancel this review and review the draft again.
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button className="button" disabled={busy} onClick={close}>
          Return to draft
        </button>
        <button
          className="button primary"
          disabled={busy || stale || !review?.changed}
          onClick={() => void apply()}
        >
          {busy
            ? "Applying…"
            : `Apply to ${review?.samples.length ?? "all"} samples`}
        </button>
      </div>
    </Modal>
  );
}
