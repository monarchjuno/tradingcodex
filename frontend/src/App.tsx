import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiErrorText, requestJSON } from "./api";
import { asRecord, asText, normalizeArtifact, normalizeCalculation, normalizeDataset, recordsFrom, Section, sectionError } from "./domain";
import { LibraryPage } from "./features/LibraryPage";
import { WikiPage } from "./features/WikiPage";
import { SystemPage } from "./features/SystemPage";
import { hashForSection, sectionFromHash } from "./navigation.js";
import { ErrorNotice, LoadingState } from "./ui";
import { ViewerShell } from "./ViewerShell";
import { sectionData, snapshotSections } from "./viewer-data.js";

export default function App() {
  const [section, setSection] = useState<Section>(() => sectionFromHash(window.location.hash) as Section);
  const [state, setState] = useState<Record<string, unknown>>({});
  const [stateLoading, setStateLoading] = useState(true);
  const [stateError, setStateError] = useState("");
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
    const labels: Record<Section, string> = { library: "Library", wiki: "Wiki", system: "System" };
    document.title = `${labels[section]} · TradingCodex`;
    requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true }));
  }, [section]);

  const artifacts = useMemo(() => recordsFrom(sectionData(state, "artifacts")).map(normalizeArtifact), [state]);
  const datasets = useMemo(() => recordsFrom(sectionData(state, "datasets")).map(normalizeDataset), [state]);
  const calculations = useMemo(() => recordsFrom(sectionData(state, "calculations")).map(normalizeCalculation), [state]);
  const libraryItems = useMemo(() => [...artifacts, ...datasets, ...calculations], [artifacts, datasets, calculations]);
  const workspaceData = asRecord(sectionData(state, "workspace"));
  const workspace = asRecord(workspaceData.context);
  const workspaceOptions = recordsFrom(workspaceData.options);
  const workspaceId = asText(workspace.workspace_id);
  const workspaceName = asText(workspace.project_name, "Local workspace");
  const workspaceChoices = workspaceOptions.map((item) => ({ id: asText(item.workspace_id), name: asText(item.project_name, asText(item.workspace_id)) }));
  const hasSnapshot = Object.keys(state).length > 0;

  const switchWorkspace = (id: string) => {
    if (!id || id === workspaceId || stateLoading) return;
    const url = new URL(window.location.href);
    url.searchParams.set("workspace", id);
    history.pushState(null, "", `${url.pathname}${url.search}${url.hash || hashForSection(section)}`);
    setState({});
    void loadState().finally(() => requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true })));
  };

  return <>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <ViewerShell mainRef={mainRef} section={section} selectedWorkspaceId={workspaceId} stateLoading={stateLoading} switchWorkspace={switchWorkspace} workspaces={workspaceChoices}>
      <div className="sr-status" aria-live="polite">{stateLoading ? "Loading TradingCodex" : stateError || `${workspaceName} ready`}</div>
      {stateError && <div className="global-notice"><ErrorNotice retry={() => void loadState()}>{stateError}</ErrorNotice></div>}
      {stateLoading && !hasSnapshot ? <div className="initial-loading"><LoadingState label="Opening the selected workspace…" /></div> : stateError && !hasSnapshot ? null : <div className="section-host" key={workspaceId}>
        {section === "library" && <LibraryPage artifacts={libraryItems} error={[sectionError(state, "artifacts"), sectionError(state, "datasets"), sectionError(state, "calculations")].filter(Boolean).join(" · ")} loading={stateLoading} />}
        {section === "wiki" && <WikiPage />}
        {section === "system" && <SystemPage state={state} />}
      </div>}
    </ViewerShell>
  </>;
}
