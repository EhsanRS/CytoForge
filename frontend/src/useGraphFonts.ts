import { useEffect, useState } from "react";
import type { GraphOptions } from "./types";
import {
  canvasGraphText,
  graphTextRoles,
  type GraphTextRole,
} from "./graphTypography";

export function useGraphFonts(options: GraphOptions) {
  const [revision, setRevision] = useState(0);
  const signature = JSON.stringify(options.typography ?? {});
  useEffect(() => {
    let cancelled = false;
    const typography = JSON.parse(signature) as GraphOptions["typography"];
    const requests = Object.keys(graphTextRoles)
      .filter((role) => typography?.[role as GraphTextRole])
      .map((role) =>
        document.fonts.load(
          canvasGraphText({ typography }, role as GraphTextRole, 12, "").font,
        ),
      );
    if (requests.length)
      void Promise.all(requests).then(() => {
        if (!cancelled) setRevision((value) => value + 1);
      });
    return () => {
      cancelled = true;
    };
  }, [signature]);
  return revision;
}
