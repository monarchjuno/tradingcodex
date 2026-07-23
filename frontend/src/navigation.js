export const SECTIONS = ["episodes", "library", "wiki", "system"];

/** @param {string} hash */
export function sectionFromHash(hash) {
  const value = hash.replace(/^#\/?/, "").split(/[/?]/, 1)[0].toLowerCase();
  return SECTIONS.includes(value) ? value : "episodes";
}

/** @param {string} section */
export function hashForSection(section) {
  return `#/${SECTIONS.includes(section) ? section : "episodes"}`;
}

/** @param {Array<unknown>} values @param {string} query */
export function matchesSearch(values, query) {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return values.some((value) => String(value ?? "").toLocaleLowerCase().includes(needle));
}
