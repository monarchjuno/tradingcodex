import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiErrorText, requestJSON } from "./api";
import { asRecord, asText, normalizeArtifact, normalizeSkill, recordsFrom, Section, sectionError, Theme } from "./domain";
import { LibraryPage } from "./features/LibraryPage";
import { SkillsPage } from "./features/SkillsPage";
import { SystemPage } from "./features/SystemPage";
import { hashForSection, sectionFromHash } from "./navigation.js";
import { ErrorNotice, LoadingState, StatusPill } from "./ui";
import { sectionData, snapshotSections } from "./viewer-data.js";

const PRIMARY_NAV: Array<{ id: Section; label: string }> = [
  { id: "library", label: "Library" },
  { id: "skills", label: "Skills" },
  { id: "system", label: "System" },
];

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = localStorage.getItem("tcx-theme");
    return stored === "dark" || stored === "light" ? stored : "auto";
  });
  useEffect(() => {
    if (theme === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    localStorage.setItem("tcx-theme", theme);
  }, [theme]);
  return [theme, () => setTheme((current) => current === "auto" ? "dark" : current === "dark" ? "light" : "auto")];
}

export default function App() {
  const [section, setSection] = useState<Section>(() => sectionFromHash(window.location.hash) as Section);
  const [theme, cycleTheme] = useTheme();
  const [state, setState] = useState<Record<string, unknown>>({});
  const [stateLoading, setStateLoading] = useState(true);
  const [stateError, setStateError] = useState("");
  const [selectedSkillId, setSelectedSkillId] = useState("");
  const mainRef = useRef<HTMLElement>(null);

  const loadState = useCallback(async () => {
    setStateLoading(true);
    setStateError("");
    try {
      const payload = await requestJSON<unknown>("/api/viewer/");
      setState(snapshotSections(payload));
    } catch (error) {
      setStateError(apiErrorText(error));
    } finally {
      setStateLoading(false);
    }
  }, []);

  useEffect(() => { void loadState(); }, [loadState]);
  useEffect(() => {
    const onHash = () => setSection(sectionFromHash(window.location.hash) as Section);
    window.addEventListener("hashchange", onHash);
    if (!window.location.hash || window.location.hash.startsWith("#/work")) {
      history.replaceState(null, "", `${window.location.pathname}${window.location.search}${hashForSection("library")}`);
      setSection("library");
    }
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    const labels: Record<Section, string> = { library: "Library", skills: "Skills", system: "System" };
    document.title = `${labels[section]} · TradingCodex`;
    window.scrollTo(0, 0);
    requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true }));
  }, [section]);

  const skills = useMemo(() => recordsFrom(sectionData(state, "skills")).map(normalizeSkill), [state]);
  const artifacts = useMemo(() => recordsFrom(sectionData(state, "artifacts")).map(normalizeArtifact), [state]);
  const workspaceData = asRecord(sectionData(state, "workspace"));
  const workspace = asRecord(workspaceData.context);
  const workspaceOptions = recordsFrom(workspaceData.options);
  const workspaceId = asText(workspace.workspace_id);
  const workspaceName = asText(workspace.project_name, "Local workspace");
  const selectedWorkspaceOption = workspaceOptions.find((item) => asText(item.workspace_id) === workspaceId) || {};
  const workspaceStatus = asText(selectedWorkspaceOption.status_label, selectedWorkspaceOption.bootstrapped === true ? "ready" : "unavailable");
  const hasSnapshot = Object.keys(state).length > 0;

  const switchWorkspace = (id: string) => {
    if (!id || id === workspaceId || stateLoading) return;
    const url = new URL(window.location.href);
    url.searchParams.set("workspace", id);
    history.pushState(null, "", `${url.pathname}${url.search}${url.hash || hashForSection(section)}`);
    setState({});
    setSelectedSkillId("");
    void loadState().finally(() => requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true })));
  };

  return <>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <div className="app-shell">
      <header className="global-header">
        <a className="brand" href={hashForSection("library")} aria-label="TradingCodex viewer"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><span><strong>TradingCodex</strong><small>Workspace viewer</small></span></a>
        <nav className="primary-nav" aria-label="Primary navigation">{PRIMARY_NAV.map((item) => <a key={item.id} href={hashForSection(item.id)} className={section === item.id ? "active" : ""} aria-current={section === item.id ? "page" : undefined}>{item.label}</a>)}</nav>
        <div className="header-tools"><span className={`service-health ${stateError ? "error" : stateLoading ? "loading" : "ready"}`} role="status" aria-label={stateError ? "Service needs attention" : stateLoading ? "Refreshing local service" : "Local viewer ready"}><i aria-hidden="true" /><span className="health-label">{stateError ? "Attention" : stateLoading ? "Refreshing" : "Read only"}</span></span><button className="theme-button" type="button" onClick={cycleTheme} aria-label={`Theme is ${theme}. Change theme.`}><span aria-hidden="true">◐</span><span className="theme-label">{theme}</span></button></div>
      </header>
      <div className="viewer-layout">
        <aside className="workspace-sidebar" aria-label="Workspace selection">
          <div className="workspace-sidebar-heading"><span className="eyebrow">Registered</span><h2>Workspaces</h2><p>Inspect one attached workspace at a time.</p></div>
          <label className="workspace-mobile-select"><span>Workspace</span><select aria-label="Workspace" value={workspaceId} disabled={stateLoading || !workspaceOptions.length} onChange={(event) => switchWorkspace(event.target.value)}>{workspaceOptions.map((item) => { const id = asText(item.workspace_id); return <option key={id} value={id}>{asText(item.project_name, id)}</option>; })}</select><span className="workspace-compact-meta"><span>{asText(workspace.git_branch, "No branch")}{workspace.git_dirty === true ? " · modified" : ""}</span><StatusPill value={workspaceStatus} /></span></label>
          <div className="workspace-list">{workspaceOptions.map((item) => {
            const id = asText(item.workspace_id);
            const selected = id === workspaceId;
            return <button key={id} type="button" className={selected ? "workspace-option selected" : "workspace-option"} aria-pressed={selected} disabled={stateLoading && !selected} onClick={() => switchWorkspace(id)}><span className="workspace-option-name">{asText(item.project_name, id)}</span><span className="workspace-option-meta">{asText(item.git_branch, "No branch")}{item.git_dirty === true ? " · modified" : ""}</span><StatusPill value={asText(item.status_label, item.bootstrapped === true ? "ready" : "unavailable")} /></button>;
          })}</div>
          {!stateLoading && !workspaceOptions.length && <p className="workspace-empty">No validated attached workspace is available.</p>}
          <div className="readonly-note"><strong>Read-only viewer</strong><span>Run analysis and make workspace changes from native Codex or <code>tcx</code>.</span></div>
        </aside>
        <main id="main-content" ref={mainRef} tabIndex={-1} aria-busy={stateLoading && !hasSnapshot}>
          <div className="sr-status" aria-live="polite">{stateLoading ? "Loading TradingCodex" : stateError || `${workspaceName} ready`}</div>
          {stateError && <div className="global-notice"><ErrorNotice retry={() => void loadState()}>{stateError}</ErrorNotice></div>}
          {stateLoading && !hasSnapshot ? <div className="initial-loading"><LoadingState label="Opening the selected workspace…" /></div> : stateError && !hasSnapshot ? null : <div key={workspaceId}>
            {section === "library" && <LibraryPage artifacts={artifacts} error={sectionError(state, "artifacts")} loading={stateLoading} />}
            {section === "skills" && <SkillsPage state={state} skills={skills.filter((skill) => skill.visible)} error={sectionError(state, "skills")} selectedSkillId={selectedSkillId} setSelectedSkillId={setSelectedSkillId} loading={stateLoading} />}
            {section === "system" && <SystemPage state={state} />}
          </div>}
        </main>
      </div>
    </div>
  </>;
}
