import { AnchorButton, Button, HTMLSelect, Icon, Navbar, NavbarGroup, NavbarHeading } from "@blueprintjs/core";
import { ReactNode, RefObject, useEffect, useState } from "react";

import { Section, Theme } from "./domain";
import { hashForSection } from "./navigation.js";

const NAV_ITEMS: Array<{ id: Section; label: string; icon: "timeline-events" | "folder-open" | "manual" | "cog" }> = [
  { id: "episodes", label: "Episodes", icon: "timeline-events" },
  { id: "library", label: "Library", icon: "folder-open" },
  { id: "wiki", label: "Wiki", icon: "manual" },
  { id: "system", label: "System", icon: "cog" },
];

type WorkspaceChoice = { id: string; name: string };

export function ViewerShell({
  children,
  mainRef,
  section,
  selectedWorkspaceId,
  stateLoading,
  switchWorkspace,
  workspaces,
}: {
  children: ReactNode;
  mainRef: RefObject<HTMLElement | null>;
  section: Section;
  selectedWorkspaceId: string;
  stateLoading: boolean;
  switchWorkspace: (id: string) => void;
  workspaces: WorkspaceChoice[];
}) {
  const [theme, cycleTheme] = useTheme();

  return <div className="app-shell">
    <Navbar className="global-header">
      <NavbarGroup align="left">
        <NavbarHeading><a className="brand" href={hashForSection("library")}><Icon icon="chart" size={18} /><strong>TradingCodex</strong></a></NavbarHeading>
      </NavbarGroup>
      <NavbarGroup className="primary-nav" align="left">{NAV_ITEMS.map((item) => <AnchorButton key={item.id} href={hashForSection(item.id)} icon={item.icon} minimal active={section === item.id} text={item.label} />)}</NavbarGroup>
      <NavbarGroup align="right"><Button aria-label={`Theme is ${theme}. Change theme.`} icon={theme === "dark" ? "moon" : theme === "light" ? "flash" : "contrast"} minimal onClick={cycleTheme} /></NavbarGroup>
    </Navbar>

    <div className="viewer-layout">
      <aside className="workspace-sidebar" aria-label="Workspace selection">
        <h2>Workspace</h2>
        <div className="workspace-list">{workspaces.map((workspace) => <Button key={workspace.id} active={workspace.id === selectedWorkspaceId} alignText="left" fill minimal onClick={() => switchWorkspace(workspace.id)} text={workspace.name} />)}</div>
        {!stateLoading && workspaces.length === 0 && <p className="muted">No attached workspace.</p>}
        <label className="workspace-mobile-select"><span>Workspace</span><HTMLSelect fill value={selectedWorkspaceId} disabled={stateLoading || workspaces.length === 0} onChange={(event) => switchWorkspace(event.target.value)}>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}</HTMLSelect></label>
      </aside>
      <main id="main-content" ref={mainRef} tabIndex={-1}>{children}</main>
    </div>
  </div>;
}

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = localStorage.getItem("tcx-theme");
    return stored === "dark" || stored === "light" ? stored : "auto";
  });

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = theme === "dark" || (theme === "auto" && media.matches);
      document.documentElement.classList.toggle("bp6-dark", dark);
      document.documentElement.dataset.theme = dark ? "dark" : "light";
      localStorage.setItem("tcx-theme", theme);
    };
    apply();
    if (theme === "auto") media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);

  return [theme, () => setTheme((current) => current === "auto" ? "dark" : current === "dark" ? "light" : "auto")];
}
