/// <reference types="vite/client" />

interface Window {
  cytoforgeDesktop?: {
    getComparisonWindow: () => Promise<
      (import("./types").DesktopComparisonState & { id: string }) | null
    >;
    openComparisonWindow: (
      state: import("./types").DesktopComparisonState,
    ) => Promise<string>;
    updateComparisonWindow: (
      state: import("./types").DesktopComparisonState,
    ) => Promise<boolean>;
    getPlotWindow: () => Promise<import("./types").DesktopPlotWindow | null>;
    getPlotSession: (workspaceId?: string) => Promise<{
      state: import("./types").DesktopPlotState | null;
      dirty: boolean;
      active: boolean;
      remembered: number;
    } | null>;
    openPlotWindow: (
      state: import("./types").DesktopPlotState,
    ) => Promise<string>;
    updatePlotWindow: (
      state: import("./types").DesktopPlotState,
      dirty: boolean,
      active?: boolean,
    ) => Promise<boolean>;
    navigatePlot: (action: import("./types").PlotNavigationAction) => Promise<{
      moved: number;
      skipped: { sample_id: string; name: string; reason: string }[];
      revision: number;
    }>;
    onPlotNavigation: (
      listener: (state: import("./types").DesktopPlotState) => void,
    ) => () => void;
    closePlotWindow: () => Promise<boolean>;
    setGateDraftDirty: (value: boolean) => void;
    notifyWorkspaceChanged: (id: string) => void;
    onWorkspaceChanged: (
      listener: (value: { workspaceId: string; revision: number }) => void,
    ) => () => void;
    exportReportPdf: (descriptor: {
      workspace: string;
      revision: number;
      title: string;
      manifest: string;
      reportHash: string;
      domHash: string;
      pageCount: number;
    }) => Promise<{
      canceled?: boolean;
      path?: string;
      bytes?: number;
      pages?: number;
      sha256?: string;
    }>;
    getReportDraft: (workspace: string) => Promise<string | null>;
    saveEventExport: (descriptor: {
      workspaceId: string;
      exportId: string;
    }) => Promise<{
      canceled?: boolean;
      path?: string;
      bytes?: number;
      sha256?: string;
    }>;
    saveReportDraft: (
      workspace: string,
      value: string | null,
    ) => Promise<boolean>;
    getWorkspace: () => Promise<string | null>;
    saveWorkspace: (value: string | null) => void;
    getPlateDraft: (workspace: string) => Promise<string | null>;
    savePlateDraft: (
      workspace: string,
      value: string | null,
    ) => Promise<boolean>;
  };
}
