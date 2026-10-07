// Machine-local view history; scientific data and unsaved gate geometry stay out.
const MAX_SESSION_BYTES = 12 * 1024 * 1024;
const keyFor = (state) =>
  `${state.workspaceId}/${state.sampleId}/${state.gateId || "all"}` +
  (state.pooled
    ? `/pooled/${state.groupId || "all"}/${state.sampleFilter || ""}`
    : "");
function viewMemory(validate, saved = [], limit = 96) {
  if (
    !Array.isArray(saved) ||
    saved.length > limit ||
    saved.some((v) => !validate(v)) ||
    new Set(saved.map(keyFor)).size !== saved.length
  )
    throw new Error("Invalid remembered plot views");
  const entries = new Map(saved.map((v) => [keyFor(v), structuredClone(v)]));
  return {
    remember(state) {
      if (!validate(state)) throw new Error("Invalid plot view");
      const key = keyFor(state);
      entries.delete(key);
      entries.set(key, structuredClone(state));
      while (entries.size > limit) entries.delete(entries.keys().next().value);
    },
    candidates(workspaceId, sampleId) {
      return [...entries.values()]
        .filter((v) => v.workspaceId === workspaceId && v.sampleId === sampleId)
        .map((v) => structuredClone(v));
    },
    latest(workspaceId) {
      const state = [...entries.values()]
        .reverse()
        .find((v) => v.workspaceId === workspaceId);
      return state ? structuredClone(state) : null;
    },
    serialize() {
      return [...entries.values()].map((v) => structuredClone(v));
    },
    get size() {
      return entries.size;
    },
  };
}
module.exports = { viewMemory, MAX_SESSION_BYTES };
