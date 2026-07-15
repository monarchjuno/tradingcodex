import { useEffect, useMemo, useRef, useState } from "react";

import { requestJSON, apiErrorText } from "../api";
import { Artifact, asRecord, asStringList, asText, formatDate, titleCase } from "../domain";
import { EmptyState, ErrorNotice, FieldList, LoadingState, PageHeader, SectionHeader, StatusPill } from "../ui";
import { collectionViewState } from "../viewer-data.js";

export function LibraryPage({ artifacts, error, loading }: { artifacts: Artifact[]; error: string; loading: boolean }) {
  const requestedArtifact = sessionStorage.getItem("tcx-selected-artifact") || "";
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const [selectedId, setSelectedId] = useState(requestedArtifact);
  const [readerOpen, setReaderOpen] = useState(Boolean(requestedArtifact));
  const [detail, setDetail] = useState<Record<string, unknown>>({});
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const indexRef = useRef<HTMLElement>(null);
  const readerRef = useRef<HTMLElement>(null);
  const types = useMemo(() => [...new Set(artifacts.map((artifact) => artifact.type))].sort(), [artifacts]);
  const filtered = artifacts.filter((artifact) => {
    const searchable = [artifact.title, artifact.id, artifact.type, artifact.summary, artifact.readiness].join(" ").toLocaleLowerCase();
    return (type === "all" || artifact.type === type) && searchable.includes(query.trim().toLocaleLowerCase());
  });
  const selected = artifacts.find((artifact) => artifact.id === selectedId) || null;
  const viewState = collectionViewState({ loading, error, count: artifacts.length });

  useEffect(() => {
    if (requestedArtifact) sessionStorage.removeItem("tcx-selected-artifact");
  }, [requestedArtifact]);

  useEffect(() => {
    if (!artifacts.length) {
      setSelectedId("");
      return;
    }
    if (!artifacts.some((artifact) => artifact.id === selectedId)) setSelectedId(artifacts[0].id);
  }, [artifacts, selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    setDetail({});
    setDetailError("");
    setDetailLoading(true);
    void requestJSON<unknown>(`/api/viewer/artifacts/${encodeURIComponent(selectedId)}/`, { signal: controller.signal })
      .then((payload) => setDetail(asRecord(payload)))
      .catch((reason) => { if (!controller.signal.aborted) setDetailError(apiErrorText(reason)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedId]);

  const choose = (artifact: Artifact) => {
    setSelectedId(artifact.id);
    setReaderOpen(true);
    requestAnimationFrame(() => {
      readerRef.current?.scrollIntoView({ block: "start" });
      readerRef.current?.focus();
    });
  };

  return <section className="page library-page" aria-labelledby="library-title">
    <PageHeader eyebrow="Research library" title="Evidence you can inspect." titleId="library-title" description="Read the conclusion, source timing, uncertainty, and missing evidence before relying on a result." action={<span className="page-count">{artifacts.length}<small>artifacts</small></span>} />
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {viewState === "loading" ? <LoadingState label="Loading workspace research…" /> : viewState === "error" ? null : viewState === "empty" ? <EmptyState title="No research artifacts yet">Run analysis from native Codex. Accepted research will appear here.</EmptyState> : <>
      <div className="library-toolbar"><label><span className="sr-only">Search research</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search titles, summaries, and types" /></label><label><span className="sr-only">Filter by artifact type</span><select value={type} onChange={(event) => setType(event.target.value)}><option value="all">All research types</option>{types.map((value) => <option key={value} value={value}>{titleCase(value)}</option>)}</select></label></div>
      <div className={`library-layout${readerOpen ? " reader-open" : ""}`}>
        <section ref={indexRef} className="artifact-index" aria-label="Research artifacts" tabIndex={-1}>
          <div className="index-heading"><span>{filtered.length} results</span>{(query || type !== "all") && <button type="button" onClick={() => { setQuery(""); setType("all"); }}>Clear filters</button>}</div>
          <div className="artifact-list">{filtered.map((artifact) => <button key={artifact.id} type="button" className={selected?.id === artifact.id ? "artifact-row selected" : "artifact-row"} aria-pressed={selected?.id === artifact.id} onClick={() => choose(artifact)}><div className="artifact-row-top"><span>{titleCase(artifact.type)}</span><StatusPill value={artifact.readiness} /></div><strong>{artifact.title}</strong><p>{artifact.summary || "Open this verified workspace artifact."}</p><span className="artifact-date">{artifact.sourceAsOf ? `Sources as of ${formatDate(artifact.sourceAsOf)}` : "Source timing not stated"}</span></button>)}{!filtered.length && <EmptyState title="No matching research">Broaden the search or clear the type filter.</EmptyState>}</div>
        </section>
        <article ref={readerRef} className="artifact-reader" aria-busy={detailLoading} tabIndex={-1}>
          <button type="button" className="mobile-back" onClick={() => { setReaderOpen(false); requestAnimationFrame(() => { indexRef.current?.scrollIntoView({ block: "start" }); indexRef.current?.focus(); }); }}>← Back to research</button>
          {selected ? <ArtifactReader artifact={selected} detail={detail} loading={detailLoading} error={detailError} /> : <EmptyState title="Choose a research artifact">Select a result to read its verified synthesis and evidence posture.</EmptyState>}
        </article>
      </div>
    </>}
  </section>;
}

function ArtifactReader({ artifact, detail, loading, error }: { artifact: Artifact; detail: Record<string, unknown>; loading: boolean; error: string }) {
  const data = { ...artifact.raw, ...detail };
  const html = asText(asRecord(detail.preview).html);
  const contrary = asStringList(data.contrary_evidence);
  const invalidation = asStringList(data.invalidation_conditions);
  const updates = asStringList(data.update_triggers);
  const blocked = asStringList(data.blocked_actions);
  const snapshots = asStringList(data.source_snapshot_ids);
  const inputs = asStringList(data.input_artifact_ids);
  const evidenceGroups = [
    ["Contrary evidence", contrary],
    ["Invalidation conditions", invalidation],
    ["Update triggers", updates],
  ] as const;
  return <>
    <header className="reader-header"><div className="reader-kicker"><span>{titleCase(artifact.type)}</span><StatusPill value={artifact.readiness} /></div><h2>{artifact.title}</h2>{artifact.summary && <p className="reader-summary">{artifact.summary}</p>}</header>
    <dl className="reader-facts"><div><dt>Confidence</dt><dd>{titleCase(artifact.confidence)}</dd></div><div><dt>Source timing</dt><dd>{artifact.sourceAsOf || "Not stated"}</dd></div><div><dt>Produced by</dt><dd>{titleCase(asText(data.producer_role, asText(data.role, "Not stated")))}</dd></div></dl>
    {asText(data.next_action) && <div className="next-action"><span className="eyebrow">What to watch next</span><p>{asText(data.next_action)}</p></div>}
    {artifact.missingEvidence.length > 0 && <div className="evidence-gap"><h3>Missing evidence</h3><FieldList values={artifact.missingEvidence} /></div>}
    {evidenceGroups.some(([, values]) => values.length) && <section className="reader-evidence"><SectionHeader eyebrow="Decision quality" title="Evidence posture" /><div className="evidence-grid">{evidenceGroups.filter(([, values]) => values.length).map(([label, values]) => <div key={label}><h3>{label}</h3><FieldList values={values} /></div>)}</div></section>}
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {loading ? <LoadingState label="Loading the sanitized research report…" /> : html ? <section className="full-report"><SectionHeader eyebrow="Verified artifact" title="Full research" /><div className="rendered-content" dangerouslySetInnerHTML={{ __html: html }} /></section> : <p className="muted">A full sanitized preview is not available for this artifact.</p>}
    {blocked.length > 0 && <details className="boundary-disclosure"><summary>Outside this analysis</summary><FieldList values={blocked} /></details>}
    <details className="technical-disclosure artifact-provenance"><summary>Artifact provenance</summary><dl><div><dt>Artifact ID</dt><dd><code>{artifact.id}</code></dd></div><div><dt>Workflow run</dt><dd><code>{asText(data.workflow_run_id, "Not stated")}</code></dd></div><div><dt>Version</dt><dd>{asText(data.version, "Not stated")}</dd></div><div><dt>Content digest</dt><dd><code>{asText(data.content_hash, "Not stated")}</code></dd></div><div><dt>Source snapshots</dt><dd>{snapshots.length}</dd></div><div><dt>Input artifacts</dt><dd>{inputs.length}</dd></div></dl>{asStringList(data.source_trust_notes).length > 0 && <FieldList values={asStringList(data.source_trust_notes)} />}</details>
  </>;
}
