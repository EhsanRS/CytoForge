// Candidate packaging uses the builder's installed-manifest collector. This
// reads the same production dependency graph without spawning an npm listing
// process, whose piped output is unavailable in this execution environment.
const collectors = require("app-builder-lib/out/node-module-collector");
const original = collectors.getCollectorByPackageManager;
collectors.getCollectorByPackageManager = function (manager, ...args) {
  return original(
    manager === collectors.PM.NPM ? collectors.PM.TRAVERSAL : manager,
    ...args,
  );
};
require("../node_modules/electron-builder/cli.js");
