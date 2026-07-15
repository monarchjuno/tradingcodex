/** @param {unknown} value @returns {Record<string, unknown>} */
function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? /** @type {Record<string, unknown>} */ (value)
    : {};
}

/** @param {unknown} value */
export function snapshotSections(value) {
  const snapshot = record(value);
  if (snapshot.sections === null || typeof snapshot.sections !== "object" || Array.isArray(snapshot.sections)) {
    throw new TypeError("Viewer snapshot is missing the canonical sections object.");
  }
  return { ...snapshot.sections, generated_at: snapshot.generated_at };
}

/** @param {Record<string, unknown>} sections @param {string} key */
export function sectionData(sections, key) {
  const section = record(sections[key]);
  return section.ok === true ? section.data : undefined;
}

/** @param {{loading: boolean, error: string, count: number}} value */
export function collectionViewState(value) {
  if (value.loading && value.count === 0) return "loading";
  if (value.error && value.count === 0) return "error";
  if (value.count === 0) return "empty";
  return "ready";
}
