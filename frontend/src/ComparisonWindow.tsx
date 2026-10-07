import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type {
  DesktopComparisonState,
  PopulationComparisonResult,
} from "./types";
import { ErrorState } from "./components/Common";
import { PopulationComparisonPlot } from "./components/PopulationComparisonPlot";

export function ComparisonWindow({
  initial,
}: {
  initial: DesktopComparisonState & { id: string };
}) {
  const [view, setView] = useState<DesktopComparisonState>(() => {
    const { id: _id, ...state } = initial;
    return state;
  });
  const [error, setError] = useState("");
  const cache = useQueryClient();
  const result = useQuery({
    queryKey: [view.workspaceId, "native-comparison", view.resultId],
    queryFn: () =>
      api<PopulationComparisonResult>(
        `/workspaces/${view.workspaceId}/population-comparison/${view.resultId}`,
      ),
  });
  useEffect(() => {
    document.title = `${result.data?.request.name ?? "Population comparison"} · CytoForge`;
  }, [result.data?.request.name]);
  useEffect(
    () =>
      window.cytoforgeDesktop?.onWorkspaceChanged((value) => {
        if (value.workspaceId === view.workspaceId)
          void cache.invalidateQueries({ queryKey: [view.workspaceId] });
      }),
    [cache, view.workspaceId],
  );
  const change = async (state: DesktopComparisonState) => {
    setView(state);
    try {
      if (!(await window.cytoforgeDesktop!.updateComparisonWindow(state)))
        setError("Window presentation could not be saved");
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <main className="native-comparison-window">
      <header>
        <h1>{result.data?.request.name ?? "Population comparison"}</h1>
        <span>Independent comparison window</span>
      </header>
      {error && <div role="alert">{error}</div>}
      {result.error && <ErrorState error={result.error} />}
      {result.data && (
        <PopulationComparisonPlot
          result={result.data}
          view={view}
          onChange={(state) => void change(state)}
        />
      )}
    </main>
  );
}
