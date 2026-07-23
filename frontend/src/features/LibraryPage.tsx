import { useEffect, useMemo, useRef, useState } from "react";
import { Button, HTMLSelect, InputGroup } from "@blueprintjs/core";

import { requestJSON, apiErrorText } from "../api";
import { Artifact, asRecord, asStringList, asText, titleCase } from "../domain";
import { EmptyState, ErrorNotice, FieldList, LoadingState, PageHeader, SectionHeader } from "../ui";
import { collectionViewState } from "../viewer-data.js";

export function LibraryPage({ artifacts, error, loading }: { artifacts: Artifact[]; error: string; loading: boolean }) {
  const requestedArtifact = requestedArtifactFromLocation() || sessionStorage.getItem("tcx-selected-artifact") || "";
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
      readerRef.current?.scrollTo({ top: 0, behavior: "smooth" });
      readerRef.current?.focus({ preventScroll: true });
    });
  };

  return <section className="page library-page" aria-labelledby="library-title">
    <PageHeader title="Research" titleId="library-title" description="Completed research, datasets, and calculations." />
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {viewState === "loading" ? <LoadingState label="Loading workspace research…" /> : viewState === "error" ? null : viewState === "empty" ? <EmptyState title="No research artifacts yet">Run analysis from native Codex. Accepted research will appear here.</EmptyState> : <>
      <div className="library-toolbar"><InputGroup aria-label="Search research" leftIcon="search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search research" /><HTMLSelect aria-label="Filter by artifact type" fill value={type} onChange={(event) => setType(event.target.value)}><option value="all">All types</option>{types.map((value) => <option key={value} value={value}>{titleCase(value)}</option>)}</HTMLSelect></div>
      <div className={`library-layout${readerOpen ? " reader-open" : ""}`}>
        <section ref={indexRef} className="artifact-index" aria-label="Research artifacts" tabIndex={-1}>
          {(query || type !== "all") && <div className="index-heading"><Button minimal small onClick={() => { setQuery(""); setType("all"); }} text="Clear filters" /></div>}
          <div className="artifact-list">{filtered.map((artifact) => <Button key={artifact.id} active={selected?.id === artifact.id} alignText="left" className="artifact-row" fill minimal aria-pressed={selected?.id === artifact.id} onClick={() => choose(artifact)}><span className="artifact-copy"><strong>{artifact.title}</strong><span>{artifact.summary || "Open this research item."}</span></span></Button>)}{!filtered.length && <EmptyState title="No matching research">Broaden the search or clear the type filter.</EmptyState>}</div>
        </section>
        <article ref={readerRef} className="artifact-reader" aria-busy={detailLoading} tabIndex={-1}>
          <Button className="mobile-back" icon="arrow-left" minimal small onClick={() => { setReaderOpen(false); requestAnimationFrame(() => indexRef.current?.focus({ preventScroll: true })); }} text="Research" />
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
  const projected = asRecord(detail.artifact);
  const status = asRecord(projected.status);
  const decisionQuality = asRecord(projected.decision_quality);
  const data = { ...artifact.raw, ...projected };
  const html = asText(asRecord(detail.preview).html);
  const contrary = asStringList(data.contrary_evidence);
  const invalidation = asStringList(decisionQuality.invalidation_conditions ?? data.invalidation_conditions);
  const updates = asStringList(decisionQuality.update_triggers ?? data.update_triggers);
  const blocked = asStringList(status.blocked_actions ?? data.blocked_actions);
  const evidenceGroups = [
    ["Contrary evidence", contrary],
    ["Invalidation conditions", invalidation],
    ["Update triggers", updates],
  ] as const;
  return <>
    <header className="reader-header"><h2>{artifact.title}</h2>{artifact.summary && <p className="reader-summary">{artifact.summary}</p>}</header>
    {asText(data.next_action) && <div className="next-action"><span className="eyebrow">What to watch next</span><p>{asText(data.next_action)}</p></div>}
    {artifact.missingEvidence.length > 0 && <div className="evidence-gap"><h3>Missing evidence</h3><FieldList values={artifact.missingEvidence} /></div>}
    {evidenceGroups.some(([, values]) => values.length) && <section className="reader-evidence"><SectionHeader eyebrow="Decision quality" title="Evidence posture" /><div className="evidence-grid">{evidenceGroups.filter(([, values]) => values.length).map(([label, values]) => <div key={label}><h3>{label}</h3><FieldList values={values} /></div>)}</div></section>}
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {loading ? <LoadingState label="Loading the sanitized research report…" /> : html ? <section className="full-report"><div className="rendered-content" dangerouslySetInnerHTML={{ __html: html }} /></section> : <p className="muted">A full sanitized preview is not available for this artifact.</p>}
    {blocked.length > 0 && <details className="boundary-disclosure"><summary>Outside this analysis</summary><FieldList values={blocked} /></details>}
  </>;
}

function requestedArtifactFromLocation(): string {
  const query = window.location.hash.split("?", 2)[1] || "";
  try { return new URLSearchParams(query).get("artifact") || ""; } catch { return ""; }
}

function DatasetReader({ artifact, detail, loading, error }: { artifact: Artifact; detail: Record<string, unknown>; loading: boolean; error: string }) {
  const manifest = asRecord(detail.dataset);
  const payload = asRecord(manifest.payload);
  const profile = asRecord(detail.profile);
  const quality = asRecord(manifest.quality);
  const columns = Array.isArray(manifest.columns) ? manifest.columns.map(asRecord) : [];
  const statistics = Array.isArray(profile.columns) ? profile.columns.map(asRecord) : [];
  return <>
    <header className="reader-header"><h2>{artifact.title}</h2><p className="reader-summary">{asText(manifest.description, artifact.summary)}</p></header>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {loading ? <LoadingState label="Loading Dataset lineage and profile…" /> : <>
      <dl className="reader-facts"><div><dt>Provider</dt><dd>{asText(manifest.provider, "Not stated")}</dd></div><div><dt>Rows</dt><dd>{asText(payload.row_count, "Not available")}</dd></div><div><dt>Knowledge cutoff</dt><dd>{asText(manifest.knowledge_cutoff, "Not stated")}</dd></div></dl>
      <section className="reader-evidence"><SectionHeader title="Dataset" /><div className="evidence-grid">{columns.map((column) => { const stats = statistics.find((item) => asText(item.name) === asText(column.name)) || {}; return <div key={asText(column.name)}><h3>{asText(column.name)}</h3><p>{asText(column.type)}{column.unit ? ` · ${asText(column.unit)}` : ""}{column.currency ? ` · ${asText(column.currency)}` : ""}</p><p>Nulls {asText(stats.null_count, "not profiled")}{stats.min !== undefined ? ` · min ${asText(stats.min)}` : ""}{stats.max !== undefined ? ` · max ${asText(stats.max)}` : ""}</p></div>; })}</div></section>
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
    <header className="reader-header"><h2>{titleCase(asText(spec.calculation_type, artifact.title))}</h2><p className="reader-summary">Recorded calculation results.</p></header>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {loading ? <LoadingState label="Loading verified Calculation Run…" /> : <>
      <dl className="reader-facts"><div><dt>Version</dt><dd>{asText(spec.calculation_version, "Not stated")}</dd></div><div><dt>Knowledge cutoff</dt><dd>{asText(spec.knowledge_cutoff, "Not stated")}</dd></div></dl>
      <section className="reader-evidence"><SectionHeader title="Metrics" /><div className="evidence-grid">{metrics.map((metric) => <div key={asText(metric.name)}><h3>{titleCase(asText(metric.name))}</h3><p>{asText(metric.value, "Not available")}{metric.unit ? ` ${asText(metric.unit)}` : ""}{metric.currency ? ` ${asText(metric.currency)}` : ""}</p></div>)}</div></section>
      {warnings.length > 0 && <div className="evidence-gap"><h3>Warnings</h3><FieldList values={warnings} /></div>}
    </>}
  </>;
}
