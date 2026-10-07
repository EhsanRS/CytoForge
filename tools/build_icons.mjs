import { Resvg } from "@resvg/resvg-js";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const svg = readFileSync(path.join(root, "desktop/assets/icon.svg"));
const png = new Resvg(svg, { fitTo: { mode: "width", value: 512 } })
  .render()
  .asPng();
writeFileSync(path.join(root, "desktop/assets/icon.png"), png);
