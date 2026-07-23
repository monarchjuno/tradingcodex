import { Button, Card, Tag } from "@blueprintjs/core";
import { useEffect, useState } from "react";

import { apiErrorText, requestJSON } from "../api";
import { asRecord, asText, RecordValue } from "../domain";
import { EmptyState, ErrorNotice, LoadingState, PageHeader, StatusText } from "../ui";

export function EpisodesPage({ episodes, error, loading }: { episodes: RecordValue[]; error: string; loading: boolean }) {
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<RecordValue>({});
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    if (!selected) { setDetail({}); return; }
    let active = true;
    setDetailLoading(true);
    setDetailError("");
    requestJSON<unknown>(`/api/viewer/episodes/${encodeURIComponent(selected)}/`)
      .then((value) => { if (active) setDetail(asRecord(asRecord(value).episode)); })
      .catch((reason) => { if (active) setDetailError(apiErrorText(reason)); })
      .finally(() => { if (active) setDetailLoading(false); });
    return () => { active = false; };
  }, [selected]);

  return <section className="page episodes-page" aria-labelledby="episodes-title">
    <PageHeader eyebrow="Decision lifecycle" title="Episodes" titleId="episodes-title" description="Follow evidence from synthesis through judgment, adoption, forecast, postmortem, and lessons." />
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {loading && !episodes.length ? <LoadingState label="Loading decision episodes…" /> : !episodes.length ? <EmptyState title="No decision episodes yet">A run appears here after TradingCodex records research or a synthesis.</EmptyState> :
      <div className="episode-layout">
        <div className="episode-list" role="list" aria-label="Decision episodes">
          {episodes.map((item) => {
            const id = asText(item.workflow_run_id);
            return <Card key={id} className={`episode-card${selected === id ? " is-selected" : ""}`} role="listitem">
              <h2>{asText(item.title, id)}</h2><p>{asText(item.summary, "No synthesis has been recorded yet.")}</p>
              <div className="episode-states"><StatusText value={asText(item.analysis_state, "researching")} /><StatusText value={asText(item.judgment_state, "not_frozen")} /><StatusText value={asText(item.forecast_state, "not_required")} /></div>
              <Button minimal small text="Open episode" aria-pressed={selected === id} onClick={() => setSelected(id)} />
            </Card>;
          })}
        </div>
        <aside className="episode-detail" aria-live="polite">
          {!selected ? <EmptyState title="Select an episode">Choose a run to inspect its judgment lifecycle and source links.</EmptyState> : detailLoading ? <LoadingState label="Opening episode…" /> : detailError ? <ErrorNotice>{detailError}</ErrorNotice> : <EpisodeDetail episode={detail} />}
        </aside>
      </div>}
  </section>;
}

function EpisodeDetail({ episode }: { episode: RecordValue }) {
  const analysis = asRecord(episode.analysis);
  const synthesis = asRecord(analysis.synthesis);
  const status = asRecord(synthesis.status);
  const memory = asRecord(episode.memory);
  const delta = asRecord(memory.delta);
  const artifacts = Array.isArray(analysis.artifacts) ? analysis.artifacts.map(asRecord) : [];
  const datasets = Array.isArray(analysis.datasets) ? analysis.datasets.map(asRecord) : [];
  const calculations = Array.isArray(analysis.calculations) ? analysis.calculations.map(asRecord) : [];
  const judgments = lifecycleItems(episode.judgment);
  const adoptions = lifecycleItems(episode.adoption);
  const forecasts = lifecycleItems(episode.forecast);
  const processReviews = lifecycleItems(episode.process_review);
  const postmortems = lifecycleItems(episode.postmortem);
  const lessons = lifecycleItems(episode.lesson);
  const updateTriggers = Array.isArray(episode.next_update_triggers) ? episode.next_update_triggers.map((item) => asText(item)).filter(Boolean) : [];
  const libraryLink = (id: string) => `#/library?artifact=${encodeURIComponent(id)}`;
  const remember = (id: string) => sessionStorage.setItem("tcx-selected-artifact", id);
  return <Card className="episode-detail-card">
    <span className="eyebrow">{asText(episode.workflow_run_id)}</span>
    <h2>{asText(synthesis.title, "Research in progress")}</h2>
    <p className="episode-conclusion">{asText(synthesis.summary, "No canonical synthesis is available for this run.")}</p>
    <div className="episode-readiness"><Tag>{asText(status.evidence_readiness, "insufficient")}</Tag><Tag>{asText(status.action_readiness, "research-only")}</Tag></div>
    <dl className="episode-facts">
      <div><dt>Judgment</dt><dd><StatusText value={asText(asRecord(episode.judgment).state)} /></dd></div>
      <div><dt>User adoption</dt><dd><StatusText value={asText(asRecord(episode.adoption).state)} /></dd></div>
      <div><dt>Forecast</dt><dd><StatusText value={asText(asRecord(episode.forecast).state)} /></dd></div>
      <div><dt>Process review</dt><dd><StatusText value={asText(asRecord(episode.process_review).state, "not_locked")} /></dd></div>
      <div><dt>Postmortem</dt><dd><StatusText value={asText(asRecord(episode.postmortem).state)} /></dd></div>
      <div><dt>Lesson</dt><dd><StatusText value={asText(asRecord(episode.lesson).state)} /></dd></div>
    </dl>
    {Object.keys(memory).length > 0 && <section><h3>Memory delta</h3><p><strong>{asText(delta.direction)}</strong> · {asText(delta.summary)}</p></section>}
    <LifecycleRecords title="Judgment snapshots" items={judgments} primary="judgment_id" secondary="recorded_at" />
    <LifecycleRecords title="User adoption" items={adoptions} primary="decision" secondary="adopted_at" />
    <LifecycleRecords title="Forecast records" items={forecasts} primary="forecast_id" secondary="event_type" />
    <LifecycleRecords title="Locked process reviews" items={processReviews} primary="id" secondary="locked_at" />
    <LifecycleRecords title="Postmortems" items={postmortems} primary="id" secondary="recorded_at" />
    <LifecycleRecords title="Lessons" items={lessons} primary="statement" secondary="lesson_state" />
    {updateTriggers.length > 0 && <section><h3>Next update triggers</h3><ul>{updateTriggers.map((item) => <li key={item}>{item}</li>)}</ul></section>}
    {artifacts.length > 0 && <section><h3>Source artifacts</h3><ul>{artifacts.map((item) => { const id = asText(item.id); return <li key={id}><a href={libraryLink(id)} onClick={() => remember(id)}>{id}</a> · {item.canonical_synthesis ? "canonical synthesis" : asText(item.role, asText(item.type))}</li>; })}</ul></section>}
    {datasets.length > 0 && <section><h3>Datasets</h3><ul>{datasets.map((item) => { const id = asText(item.id); return <li key={id}><a href={libraryLink(id)} onClick={() => remember(id)}>{id}</a></li>; })}</ul></section>}
    {calculations.length > 0 && <section><h3>Calculations</h3><ul>{calculations.map((item) => { const id = asText(item.id); return <li key={id}><a href={libraryLink(id)} onClick={() => remember(id)}>{id}</a></li>; })}</ul></section>}
  </Card>;
}

function lifecycleItems(value: unknown): RecordValue[] {
  const items = asRecord(value).items;
  return Array.isArray(items) ? items.map(asRecord) : [];
}

function LifecycleRecords({ title, items, primary, secondary }: { title: string; items: RecordValue[]; primary: string; secondary: string }) {
  if (!items.length) return null;
  return <section className="episode-records"><h3>{title}</h3><ul>{items.slice(0, 20).map((item, index) => {
    const label = asText(item[primary], asText(item.id, asText(item.path, `Record ${index + 1}`)));
    const detail = asText(item[secondary], asText(item.path));
    return <li key={`${label}:${index}`}><strong>{label}</strong>{detail && detail !== label ? <span>{detail}</span> : null}{asText(item.path) ? <code>{asText(item.path)}</code> : null}</li>;
  })}</ul></section>;
}
