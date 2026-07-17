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
    const selectedResource = artifacts.find((artifact) => artifact.id === selectedId);
    if (!selectedResource) return () => controller.abort();
    void requestJSON<unknown>(selectedResource.detailPath, { signal: controller.signal })
      .then((payload) => setDetail(asRecord(payload)))
      .catch((reason) => { if (!controller.signal.aborted) setDetailError(apiErrorText(reason)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [artifacts, selectedId]);

  const choose = (artifact: Artifact) => {
    setSelectedId(artifact.id);
    setReaderOpen(true);
    requestAnimationFrame(() => {
      readerRef.current?.scrollIntoView({ block: "start" });
      readerRef.current?.focus();
    });
  };

  return <section className="page library-page" aria-labelledby="library-title">
    <PageHeader eyebrow="Research library" title="Evidence you can inspect." titleId="library-title" description="Read artifacts, immutable Dataset lineage, and reproducible Calculation Runs without starting or changing a workflow." action={<span className="page-count">{artifacts.length}<small>objects</small></span>} />
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
  if (artifact.resourceKind === "dataset") {
    return <DatasetReader artifact={artifact} detail={detail} loading={loading} error={error} />;
  }
  if (artifact.resourceKind === "calculation") {
    return <CalculationReader artifact={artifact} detail={detail} loading={loading} error={error} />;
  }
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

function DatasetReader({ artifact, detail, loading, error }: { artifact: Artifact; detail: Record<string, unknown>; loading: boolean; error: string }) {
  const manifest = asRecord(detail.dataset);
  const payload = asRecord(manifest.payload);
  const lineage = asRecord(manifest.lineage);
  const profile = asRecord(detail.profile);
  const quality = asRecord(manifest.quality);
  const columns = Array.isArray(manifest.columns) ? manifest.columns.map(asRecord) : [];
  const statistics = Array.isArray(profile.columns) ? profile.columns.map(asRecord) : [];
  const sourceIds = asStringList(manifest.source_snapshot_ids);
  const parents = asStringList(lineage.parent_dataset_ids);
  return <>
    <header className="reader-header"><div className="reader-kicker"><span>Immutable Dataset</span><StatusPill value={detail.withdrawn === true ? "withdrawn" : detail.payload_available === true ? "available" : "missing"} /></div><h2>{artifact.title}</h2><p className="reader-summary">{asText(manifest.description, artifact.summary)}</p></header>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {loading ? <LoadingState label="Loading Dataset lineage and profile…" /> : <>
      <dl className="reader-facts"><div><dt>Provider</dt><dd>{asText(manifest.provider, "Not stated")}</dd></div><div><dt>Rows</dt><dd>{asText(payload.row_count, "Not available")}</dd></div><div><dt>Knowledge cutoff</dt><dd>{asText(manifest.knowledge_cutoff, "Not stated")}</dd></div></dl>
      <section className="reader-evidence"><SectionHeader eyebrow="Schema and profile" title="Bounded Dataset view" /><div className="evidence-grid">{columns.map((column) => { const stats = statistics.find((item) => asText(item.name) === asText(column.name)) || {}; return <div key={asText(column.name)}><h3>{asText(column.name)}</h3><p>{asText(column.type)}{column.unit ? ` · ${asText(column.unit)}` : ""}{column.currency ? ` · ${asText(column.currency)}` : ""}</p><p>Nulls {asText(stats.null_count, "not profiled")}{stats.min !== undefined ? ` · min ${asText(stats.min)}` : ""}{stats.max !== undefined ? ` · max ${asText(stats.max)}` : ""}</p></div>; })}</div></section>
      <details className="technical-disclosure artifact-provenance" open><summary>Dataset lineage</summary><dl><div><dt>Dataset ID</dt><dd><code>{artifact.id}</code></dd></div><div><dt>Payload digest</dt><dd><code>{asText(payload.sha256, "Not available")}</code></dd></div><div><dt>Source snapshots</dt><dd>{sourceIds.length}</dd></div><div><dt>Parent datasets</dt><dd>{parents.length}</dd></div></dl>{[...sourceIds, ...parents].length > 0 && <FieldList values={[...sourceIds, ...parents]} />}</details>
      {asStringList(quality.warnings).length > 0 && <div className="evidence-gap"><h3>Quality warnings</h3><FieldList values={asStringList(quality.warnings)} /></div>}
    </>}
  </>;
}

function CalculationReader({ artifact, detail, loading, error }: { artifact: Artifact; detail: Record<string, unknown>; loading: boolean; error: string }) {
  const run = asRecord(detail.run);
  const spec = asRecord(detail.spec);
  const metrics = Array.isArray(run.metrics) ? run.metrics.map(asRecord) : [];
  const warnings = asStringList(run.warnings);
  return <>
    <header className="reader-header"><div className="reader-kicker"><span>Calculation Run</span><StatusPill value={asText(run.status, artifact.readiness)} /></div><h2>{titleCase(asText(spec.calculation_type, artifact.title))}</h2><p className="reader-summary">Version {asText(spec.calculation_version, "not stated")} · exact fingerprint and runtime identity recorded.</p></header>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {loading ? <LoadingState label="Loading verified Calculation Run…" /> : <>
      <dl className="reader-facts"><div><dt>Workflow run</dt><dd><code>{asText(run.workflow_run_id, "Not stated")}</code></dd></div><div><dt>Knowledge cutoff</dt><dd>{asText(spec.knowledge_cutoff, "Not stated")}</dd></div><div><dt>Reuse origin</dt><dd><code>{asText(run.original_run_id, "Original execution")}</code></dd></div></dl>
      <section className="reader-evidence"><SectionHeader eyebrow="Typed result" title="Recorded metrics" /><div className="evidence-grid">{metrics.map((metric) => <div key={asText(metric.name)}><h3>{titleCase(asText(metric.name))}</h3><p>{asText(metric.value, "Not available")}{metric.unit ? ` ${asText(metric.unit)}` : ""}{metric.currency ? ` ${asText(metric.currency)}` : ""}</p></div>)}</div></section>
      {warnings.length > 0 && <div className="evidence-gap"><h3>Warnings</h3><FieldList values={warnings} /></div>}
      <details className="technical-disclosure artifact-provenance" open><summary>Calculation provenance</summary><dl><div><dt>Run ID</dt><dd><code>{artifact.id}</code></dd></div><div><dt>Spec ID</dt><dd><code>{asText(run.calculation_spec_id, "Not stated")}</code></dd></div><div><dt>Fingerprint</dt><dd><code>{asText(run.fingerprint, "Not stated")}</code></dd></div><div><dt>Run digest</dt><dd><code>{asText(run.run_sha256, "Not stated")}</code></dd></div></dl></details>
    </>}
  </>;
}
