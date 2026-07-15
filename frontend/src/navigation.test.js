import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { hashForSection, matchesSearch, sectionFromHash } from "./navigation.js";
import { collectionViewState, sectionData, snapshotSections } from "./viewer-data.js";

test("hash navigation stays inside the three viewer sections", () => {
  assert.equal(sectionFromHash("#/skills"), "skills");
  assert.equal(sectionFromHash("#library?artifact=a"), "library");
  assert.equal(sectionFromHash("#/work"), "library");
  assert.equal(sectionFromHash("#/unknown"), "library");
  assert.equal(hashForSection("system"), "#/system");
  assert.equal(hashForSection("admin"), "#/library");
});

test("search matches labels and metadata without case sensitivity", () => {
  assert.equal(matchesSearch(["Evidence Review", "fundamental-analyst"], "FUNDAMENTAL"), true);
  assert.equal(matchesSearch(["Evidence Review"], "forecast"), false);
  assert.equal(matchesSearch([], ""), true);
});

test("viewer data accepts only the current snapshot contract", () => {
  const sections = snapshotSections({
    generated_at: "2026-07-11T00:00:00Z",
    sections: {
      strategies: { ok: true, data: [{ name: "strategy-quality" }] },
      optional_skills: { ok: true, data: { optional_skills: [{ name: "quality-check" }] } },
      failed: { ok: false, error: { message: "unavailable" } },
    },
  });
  assert.deepEqual(sectionData(sections, "strategies"), [{ name: "strategy-quality" }]);
  assert.deepEqual(sectionData(sections, "optional_skills"), { optional_skills: [{ name: "quality-check" }] });
  assert.equal(sectionData(sections, "failed"), undefined);
  assert.throws(() => snapshotSections({ state: { strategies: [] } }), /canonical sections object/);
});

test("collection states never present an error as an empty result", () => {
  assert.equal(collectionViewState({ loading: true, error: "", count: 0 }), "loading");
  assert.equal(collectionViewState({ loading: false, error: "unavailable", count: 0 }), "error");
  assert.equal(collectionViewState({ loading: false, error: "", count: 0 }), "empty");
  assert.equal(collectionViewState({ loading: true, error: "", count: 3 }), "ready");
});

test("light theme secondary text keeps normal-text contrast", async () => {
  const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
  const light = css.match(/:root\[data-theme="light"\][\s\S]*?--ink-3:\s*(#[0-9a-f]{6})/i)?.[1];
  assert.ok(light);
  assert.ok(contrast(light, "#f4f3ee") >= 4.5);
});

test("system status labels wrap at every viewport width", async () => {
  const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
  const mobileRules = css.indexOf("@media (max-width: 600px)");
  const wrapRule = css.indexOf(".workspace-settings .status-pill");
  assert.match(
    css,
    /\.workspace-settings \.status-pill\s*\{[^}]*max-width:\s*100%[^}]*overflow-wrap:\s*anywhere[^}]*white-space:\s*normal/s,
  );
  assert.ok(wrapRule >= 0 && wrapRule < mobileRules, "the overflow guard must not be mobile-only");
});

test("narrow detail transitions preserve keyboard focus", async () => {
  const [skills, library] = await Promise.all([
    readFile(new URL("./features/SkillsPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("./features/LibraryPage.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(skills, /detailRef\.current\?\.focus\(\)/);
  assert.match(skills, /indexRef\.current\?\.focus\(\)/);
  assert.match(library, /readerRef\.current\?\.focus\(\)/);
  assert.match(library, /indexRef\.current\?\.focus\(\)/);
});

test("workspace switching returns focus to the updated main view", async () => {
  const app = await readFile(new URL("./App.tsx", import.meta.url), "utf8");
  assert.match(app, /loadState\(\)\.finally\(\(\) => requestAnimationFrame\(\(\) => mainRef\.current\?\.focus\(\{ preventScroll: true \}\)\)\)/);
});

test("half-width desktop uses the compact workspace and single-pane reader layout", async () => {
  const [css, app] = await Promise.all([
    readFile(new URL("./styles.css", import.meta.url), "utf8"),
    readFile(new URL("./App.tsx", import.meta.url), "utf8"),
  ]);
  const compactStart = css.indexOf("@media (max-width: 1099px)");
  const mobileStart = css.indexOf("@media (max-width: 700px)");
  assert.ok(compactStart >= 0 && mobileStart > compactStart);
  const compact = css.slice(compactStart, mobileStart);
  assert.match(compact, /\.viewer-layout\s*\{\s*display:\s*block/);
  assert.match(compact, /\.workspace-mobile-select\s*\{\s*display:\s*grid/);
  assert.match(compact, /\.library-layout, \.method-layout\s*\{\s*display:\s*block/);
  assert.match(compact, /\.artifact-reader, \.method-detail\s*\{\s*display:\s*none/);
  assert.doesNotMatch(compact, /--header-height:\s*118px/);
  assert.match(app, /workspace-compact-meta/);
});

test("the app exposes no work execution surface", async () => {
  const app = await readFile(new URL("./App.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(app, /run_start|run_preview|follow-up|WorkPage|mutation\(/i);
  assert.match(app, /Read-only viewer/);
  assert.match(app, /Workspaces/);
});

function contrast(left, right) {
  const luminance = (color) => {
    const channels = color.match(/[0-9a-f]{2}/gi).map((value) => Number.parseInt(value, 16) / 255);
    const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  };
  const [lighter, darker] = [luminance(left), luminance(right)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}
