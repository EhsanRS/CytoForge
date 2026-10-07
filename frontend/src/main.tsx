import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ComparisonWindow } from "./ComparisonWindow";
import { ErrorState } from "./components/Common";
import "./styles.css";

const client = new QueryClient({
  defaultOptions: {
    queries: { retry: false, staleTime: 15000, gcTime: 60000 },
  },
});
const root = createRoot(document.getElementById("root")!);
async function launch() {
  if (new URLSearchParams(location.search).has("comparisonWindow")) {
    const comparison = await window.cytoforgeDesktop?.getComparisonWindow();
    if (!comparison)
      throw new Error("Open comparison windows from the desktop workspace.");
    root.render(
      <StrictMode>
        <QueryClientProvider client={client}>
          <ComparisonWindow initial={comparison} />
        </QueryClientProvider>
      </StrictMode>,
    );
    return;
  }
  const plotWindow = await window.cytoforgeDesktop?.getPlotWindow();
  if (new URLSearchParams(location.search).has("plotWindow") && !plotWindow)
    throw new Error(
      "This plot window is unavailable. Open it from the desktop workspace.",
    );
  root.render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <App plotWindow={plotWindow ?? null} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
void launch().catch((error) => root.render(<ErrorState error={error} />));
