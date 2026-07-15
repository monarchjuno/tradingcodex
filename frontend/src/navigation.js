export const SECTIONS = ["library", "skills", "system"];

/** @param {string} hash */
export function sectionFromHash(hash) {
  const value = hash.replace(/^#\/?/, "").split(/[/?]/, 1)[0].toLowerCase();
  return SECTIONS.includes(value) ? value : "library";
}

/** @param {string} section */
export function hashForSection(section) {
  return `#/${SECTIONS.includes(section) ? section : "library"}`;
}

/** @param {Array<unknown>} values @param {string} query */
export function matchesSearch(values, query) {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return values.some((value) => String(value ?? "").toLocaleLowerCase().includes(needle));
}
