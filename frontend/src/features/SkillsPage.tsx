import { ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { apiErrorText, requestJSON } from "../api";
import { asRecord, asText, recordsFrom, sectionError, Skill, titleCase } from "../domain";
import { EmptyState, ErrorNotice, LoadingState, Notice, PageHeader, SectionHeader, StatusPill } from "../ui";
import { collectionViewState, sectionData } from "../viewer-data.js";

type SkillsPageProps = {
  state: Record<string, unknown>;
  skills: Skill[];
  error: string;
  selectedSkillId: string;
  setSelectedSkillId: (id: string) => void;
  loading: boolean;
};

export function SkillsPage({ state, skills, error, selectedSkillId, setSelectedSkillId, loading }: SkillsPageProps) {
  const [view, setView] = useState<"methods" | "extensions">("methods");
  const methodState = collectionViewState({ loading, error, count: skills.length });
  return <section className="page skills-page" aria-labelledby="skills-title">
    <PageHeader eyebrow="Codex capabilities" title="Skills projected into this workspace." titleId="skills-title" description="Inspect guidance, ownership, and authority boundaries here. Invoke skills from a native Codex task." action={<span className="page-count">{skills.length}<small>available</small></span>} />
    <div className="local-tabs" role="tablist" aria-label="Skill views"><button type="button" role="tab" aria-selected={view === "methods"} className={view === "methods" ? "active" : ""} onClick={() => setView("methods")}>Skills</button><button type="button" role="tab" aria-selected={view === "extensions"} className={view === "extensions" ? "active" : ""} onClick={() => setView("extensions")}>Strategies & extensions</button></div>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {view === "methods" ? methodState === "error" ? null : <SkillCatalog skills={skills} selectedSkillId={selectedSkillId} setSelectedSkillId={setSelectedSkillId} loading={methodState === "loading"} /> : <ExtensionCatalog state={state} />}
  </section>;
}

function SkillCatalog({ skills, selectedSkillId, setSelectedSkillId, loading }: { skills: Skill[]; selectedSkillId: string; setSelectedSkillId: (id: string) => void; loading: boolean }) {
  const [query, setQuery] = useState("");
  const [detailOpen, setDetailOpen] = useState(Boolean(selectedSkillId));
  const [detail, setDetail] = useState<Record<string, unknown>>({});
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const indexRef = useRef<HTMLElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const filtered = useMemo(() => skills.filter((skill) => [skill.label, skill.id, skill.description, skill.owner, skill.kind].join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [query, skills]);
  const selected = skills.find((skill) => skill.id === selectedSkillId) || filtered[0] || null;

  useEffect(() => {
    if (!selected?.id) return;
    const controller = new AbortController();
    setDetail({});
    setDetailError("");
    setDetailLoading(true);
    void requestJSON<unknown>(`/api/viewer/skills/${encodeURIComponent(selected.id)}/`, { signal: controller.signal })
      .then((payload) => setDetail(asRecord(payload)))
      .catch((reason) => { if (!controller.signal.aborted) setDetailError(apiErrorText(reason)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selected?.id]);

  const choose = (skill: Skill) => {
    setSelectedSkillId(skill.id);
    setDetailOpen(true);
    requestAnimationFrame(() => {
      detailRef.current?.scrollIntoView({ block: "start" });
      detailRef.current?.focus();
    });
  };
  if (loading && !skills.length) return <LoadingState label="Loading workspace skills…" />;
  if (!skills.length) return <EmptyState title="No skills are available">Check the generated skill projection with <code>./tcx doctor --layer improvement</code>.</EmptyState>;
  const detailHtml = asText(asRecord(detail.preview).html);

  return <div className={`method-layout${detailOpen ? " detail-open" : ""}`}>
    <aside ref={indexRef} className="method-index" aria-label="Workspace skills" tabIndex={-1}><label><span className="sr-only">Search skills</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search evidence, valuation, risk…" /></label><div className="method-list">{filtered.map((skill) => <button key={skill.id} type="button" className={selected?.id === skill.id ? "method-row selected" : "method-row"} aria-pressed={selected?.id === skill.id} onClick={() => choose(skill)}><span className="method-owner">{titleCase(skill.kind)} · {titleCase(skill.owner)}</span><strong>{skill.label}</strong><p>{skill.description || "A TradingCodex skill."}</p><span className="method-capability">{skill.availableInCodex ? "Native Codex" : "Unavailable"}</span></button>)}{!filtered.length && <EmptyState title="No matching skills">Try a broader capability or owner.</EmptyState>}</div></aside>
    <article ref={detailRef} className="method-detail" aria-busy={detailLoading} tabIndex={-1}><button type="button" className="mobile-back" onClick={() => { setDetailOpen(false); requestAnimationFrame(() => { indexRef.current?.scrollIntoView({ block: "start" }); indexRef.current?.focus(); }); }}>← Back to skills</button>{selected ? <><header><div className="reader-kicker"><span>{titleCase(selected.kind)}</span><StatusPill value={selected.status} /></div><h2>{selected.label}</h2><p className="reader-summary">{selected.description || "A built-in TradingCodex skill."}</p></header><section className="method-answers"><div><span className="eyebrow">Invocation</span><p><code>${selected.id}</code> from a native Codex task</p></div><div><span className="eyebrow">Owned by</span><p>{titleCase(selected.owner)}</p></div><div><span className="eyebrow">Authority boundary</span><p>{selected.boundary}</p></div></section>{detailError && <ErrorNotice>{detailError}</ErrorNotice>}{detailLoading ? <LoadingState label="Loading skill guidance…" compact /> : detailHtml && <details className="method-guidance"><summary>Read skill guidance</summary><div className="rendered-content compact" dangerouslySetInnerHTML={{ __html: detailHtml }} /></details>}<Notice title="Available in native Codex">This viewer cannot invoke or modify skills. Start a Codex task in the selected workspace and use <code>${selected.id}</code>.</Notice></> : <EmptyState title="Choose a skill">Select a skill to inspect its guidance and boundary.</EmptyState>}</article>
  </div>;
}

function ExtensionCatalog({ state }: { state: Record<string, unknown> }) {
  const strategies = recordsFrom(sectionData(state, "strategies"));
  const optionalSkills = recordsFrom(sectionData(state, "optional_skills"), "optional_skills");
  return <section className="extensions-view" aria-labelledby="extensions-title"><div className="extensions-intro"><div><span className="eyebrow">Workspace overlays</span><h2 id="extensions-title">Inspect projected extensions.</h2><p>Strategies and optional role skills guide analysis without changing agent identity, permissions, policy, approval authority, or execution authority.</p></div><aside><strong>Manage from native surfaces</strong><p>Create and update these bundles with their <code>tcx</code> commands or a native Codex task. This page is intentionally read-only.</p></aside></div><div className="extension-columns"><ExtensionList title="Strategies" count={strategies.length} error={sectionError(state, "strategies")} empty="No custom strategies are installed.">{strategies.map((item, index) => { const name = asText(item.name, asText(item.id, `strategy-${index}`)); return <div className="extension-row" key={name}><div><strong>{asText(item.label, titleCase(name))}</strong><span>{asText(item.description, "Workspace strategy overlay")}</span></div><StatusPill value={asText(item.status, "draft")} /></div>; })}</ExtensionList><ExtensionList title="Optional role skills" count={optionalSkills.length} error={sectionError(state, "optional_skills")} empty="No optional role skills are installed.">{optionalSkills.map((item, index) => { const name = asText(item.name, `skill-${index}`); return <div className="extension-row" key={`${asText(item.role)}-${name}`}><div><strong>{titleCase(name)}</strong><span>{titleCase(asText(item.role, "Unassigned role"))}</span></div><StatusPill value={asText(item.status, "draft")} /></div>; })}</ExtensionList></div></section>;
}

function ExtensionList({ title, count, error, empty, children }: { title: string; count: number; error: string; empty: string; children: ReactNode }) {
  return <section className="extension-section"><SectionHeader title={title} aside={<span className="count">{count}</span>} />{error && <ErrorNotice>{error}</ErrorNotice>}<div className="extension-list">{count ? children : !error && <p className="muted">{empty}</p>}</div></section>;
}
