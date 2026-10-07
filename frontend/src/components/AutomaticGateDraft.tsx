import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import {
  formatNumber,
  type Gate,
  type GateDimension,
  type Transform,
} from "../types";
import { GateShapeOverlay } from "./GateShapeOverlay";

export interface AutomaticGateSource {
  revision: number;
  sample_id: string;
  scope?: import("../types").PooledScope;
  parent_id: string | null;
  coordinate_gate_id: string | null;
  x: string;
  y: string;
  x_transform: Transform;
  y_transform: Transform;
  x_dimension?: GateDimension;
  y_dimension?: GateDimension;
  seed: [number, number];
}
interface AutomaticPreview {
  gate: Gate;
  count: number;
  parent_count: number;
  percent_parent: number;
  audit: {
    estimated_probability: number;
    component_count: number;
    hole_count: number;
    vertex_count: number;
    finite_parent_count: number;
  };
}

export function AutomaticGateDraft({
  workspaceId,
  source,
  bounds,
  width,
  height,
  stale,
  onUse,
  onCancel,
}: {
  workspaceId: string;
  source: AutomaticGateSource;
  bounds: number[];
  width: number;
  height: number;
  stale: boolean;
  onUse: (gate: Gate) => void;
  onCancel: () => void;
}) {
  const [coverage, setCoverage] = useState("90");
  const [sigma, setSigma] = useState("1.2");
  const [bins, setBins] = useState("128");
  const [domainText, setDomainText] = useState("");
  const domainParts = domainText.split(",");
  const domain = domainText.trim() ? domainParts.map(Number) : null;
  const validDomain =
    domain === null ||
    (domain.length === 4 &&
      domainParts.every((p) => !!p.trim()) &&
      domain.every(Number.isFinite) &&
      domain[0] < domain[1] &&
      domain[2] < domain[3]);
  const valid =
    !!coverage.trim() &&
    !!sigma.trim() &&
    !!bins.trim() &&
    validDomain &&
    Number(coverage) >= 1 &&
    Number(coverage) <= 99.5 &&
    Number(sigma) >= 0 &&
    Number(sigma) <= 4 &&
    Number.isInteger(Number(bins)) &&
    Number(bins) >= 32 &&
    Number(bins) <= 384;
  const request = {
    ...source,
    coverage: Number(coverage) / 100,
    sigma: Number(sigma),
    bins: Number(bins),
    domain,
  };
  const [debounced, setDebounced] = useState(request);
  const requestKey = JSON.stringify(request);
  const settled = requestKey === JSON.stringify(debounced);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(JSON.parse(requestKey)), 180);
    return () => clearTimeout(timer);
  }, [requestKey]);
  const query = useQuery({
    queryKey: ["automatic-gate-preview", workspaceId, debounced],
    enabled: valid && settled && !stale,
    retry: false,
    gcTime: 0,
    placeholderData: keepPreviousData,
    queryFn: ({ signal }) =>
      api<AutomaticPreview>(
        `/workspaces/${workspaceId}/gates/automatic-preview`,
        {
          method: "POST",
          body: JSON.stringify(debounced),
          signal,
        },
      ),
  });
  const preview = query.data;
  const ready =
    valid &&
    settled &&
    !stale &&
    !!preview &&
    !query.isFetching &&
    !query.isPlaceholderData &&
    !query.error;
  return (
    <>
      {preview && (
        <GateShapeOverlay
          gate={preview.gate}
          bounds={bounds}
          width={width}
          height={height}
          disabled
          onChange={() => {}}
        />
      )}
      <div
        className="automatic-gate-controls"
        role="region"
        aria-label="Automatic density gate review"
        onPointerDown={(event) => event.stopPropagation()}
        onPointerMove={(event) => event.stopPropagation()}
      >
        <strong>Automatic density gate</strong>
        <div className="automatic-gate-settings">
          <label>
            Density coverage (%)
            <input
              aria-label="Automatic density coverage"
              type="number"
              min={1}
              max={99.5}
              step={0.5}
              value={coverage}
              disabled={stale}
              onChange={(e) => setCoverage(e.target.value)}
            />
          </label>
          <label>
            Smoothing (bins)
            <input
              aria-label="Automatic density smoothing"
              type="number"
              min={0}
              max={4}
              step={0.1}
              value={sigma}
              disabled={stale}
              onChange={(e) => setSigma(e.target.value)}
            />
          </label>
          <label>
            Resolution
            <input
              aria-label="Automatic density resolution"
              type="number"
              min={32}
              max={384}
              step={16}
              value={bins}
              disabled={stale}
              onChange={(e) => setBins(e.target.value)}
            />
          </label>
        </div>
        <label className="field">
          Density bounds (optional: X min, X max, Y min, Y max)
          <input
            aria-label="Automatic density bounds"
            value={domainText}
            placeholder="Full finite parent extent"
            disabled={stale}
            onChange={(e) => setDomainText(e.target.value)}
          />
        </label>
        {!valid && (
          <p role="alert">
            Enter coverage from 1–99.5%, smoothing from 0–4 and an integer
            resolution from 32–384. Optional density bounds require four finite,
            increasing limits.
          </p>
        )}
        {(!settled || query.isFetching) && (
          <p role="status">Calculating the full parent population…</p>
        )}
        {query.error && <p role="alert">{query.error.message}</p>}
        {preview && (
          <div className="automatic-gate-count" aria-live="polite">
            <strong>
              {formatNumber(preview.count, 0)} /{" "}
              {formatNumber(preview.parent_count, 0)} events (
              {preview.percent_parent.toFixed(2)}% of parent)
            </strong>
            <p>
              {formatNumber(preview.audit.vertex_count, 0)} vertices ·{" "}
              {preview.audit.hole_count} excluded rings ·{" "}
              {preview.audit.component_count} density regions
            </p>
            <p>
              Coverage applies to the density of all{" "}
              {formatNumber(preview.audit.finite_parent_count, 0)} finite parent
              events. The selected region's saved polygon uses the event count
              shown above.
            </p>
          </div>
        )}
        <div className="automatic-gate-actions">
          <button
            className="button small primary"
            data-accept-automatic-gate
            disabled={!ready}
            onClick={() => ready && onUse(preview!.gate)}
          >
            Use automatic gate
          </button>
          <button className="button small" onClick={onCancel}>
            Cancel automatic gate
          </button>
        </div>
      </div>
    </>
  );
}
