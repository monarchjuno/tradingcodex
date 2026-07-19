import { useEffect, useMemo, useRef, useState } from "react";

import { apiErrorText, requestJSON } from "../api";
import { asRecord, asStringList, asText, normalizeWikiPage, recordsFrom, WikiPageCard } from "../domain";
import { EmptyState, ErrorNotice, LoadingState, PageHeader, StatusPill } from "../ui";

type WikiOption = { wikiId: string; origin: string; version: string };

function selectionFromHash(): { wikiId: string; path: string } | null {
  const match = window.location.hash.match(/^#\/wiki\/([^/]+)\/(.+)$/);
  if (!match) return null;
  try {
    return { wikiId: decodeURIComponent(match[1]), path: match[2].split("/").map(decodeURIComponent).join("/") };
  } catch {
    return null;
  }
}

function pageHash(page: { wikiId: string; path: string }): string {
  const encodedPath = page.path.split("/").map(encodeURIComponent).join("/");
  return `#/wiki/${encodeURIComponent(page.wikiId)}/${encodedPath}`;
}

function detailPath(page: { wikiId: string; path: string }): string {
  return `/api/viewer/wiki-pages/${encodeURIComponent(page.wikiId)}/${page.path.split("/").map(encodeURIComponent).join("/")}/`;
}

export function WikiPage({ loading: workspaceLoading }: { loading: boolean }) {
  const requested = selectionFromHash();
  const [wiki, setWiki] = useState(requested?.wikiId || "all");
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [pages, setPages] = useState<WikiPageCard[]>([]);
  const [wikis, setWikis] = useState<WikiOption[]>([]);
  const [selectedKey, setSelectedKey] = useState(requested ? `${requested.wikiId}:${requested.path}` : "");
  const [readerOpen, setReaderOpen] = useState(Boolean(requested));
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [detail, setDetail] = useState<Record<string, unknown>>({});
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const indexRef = useRef<HTMLElement>(null);
  const readerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ wiki, limit: "200" });
    if (query.trim()) params.set("q", query.trim());
    if (type) params.set("type", type);
    if (status) params.set("status", status);
    setListLoading(true);
    setListError("");
    void requestJSON<unknown>(`/api/viewer/wiki-pages/?${params}`, { signal: controller.signal })
      .then((payload) => {
        const record = asRecord(payload);
        setPages(recordsFrom(record.pages).map(normalizeWikiPage));
        setWikis(recordsFrom(record.wikis).map((item) => ({
          wikiId: asText(item.wiki_id),
          origin: asText(item.origin),
          version: asText(item.version),
        })));
      })
      .catch((reason) => { if (!controller.signal.aborted) setListError(apiErrorText(reason)); })
      .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [wiki, query, type, status]);

  useEffect(() => {
    const selectHashPage = () => {
      const next = selectionFromHash();
      if (!next) return;
      setQuery("");
      setType("");
      setStatus("");
      setWiki(next.wikiId);
      setSelectedKey(`${next.wikiId}:${next.path}`);
      setReaderOpen(true);
    };
    window.addEventListener("hashchange", selectHashPage);
    return () => window.removeEventListener("hashchange", selectHashPage);
  }, [wiki]);

  useEffect(() => {
    if (!pages.length) {
      const hashPage = selectionFromHash();
      const hashKey = hashPage ? `${hashPage.wikiId}:${hashPage.path}` : "";
      if (hashKey !== selectedKey) {
        setDetail({});
        if (!listLoading) setSelectedKey("");
      }
      return;
    }
    if (!pages.some((page) => `${page.wikiId}:${page.path}` === selectedKey)) {
      const hashPage = selectionFromHash();
      if (hashPage && `${hashPage.wikiId}:${hashPage.path}` === selectedKey) return;
      const first = pages[0];
      setSelectedKey(`${first.wikiId}:${first.path}`);
    }
  }, [pages, selectedKey, listLoading]);

  const listedSelection = pages.find((page) => `${page.wikiId}:${page.path}` === selectedKey) || null;
  const hashSelection = selectionFromHash();
  const selected = listedSelection || (hashSelection && `${hashSelection.wikiId}:${hashSelection.path}` === selectedKey ? {
    wikiId: hashSelection.wikiId,
    path: hashSelection.path,
    title: hashSelection.path.split("/").at(-1)?.replace(/\.md$/, "").replaceAll("-", " ") || "Wiki page",
    summary: "",
    type: "concept",
    status: "draft",
    aliases: [],
    tags: [],
    updatedAt: "",
    sourceCount: 0,
    backlinkCount: 0,
    raw: {},
  } satisfies WikiPageCard : null);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setDetail({});
    setDetailError("");
    setDetailLoading(true);
    void requestJSON<unknown>(detailPath(selected), { signal: controller.signal })
      .then((payload) => setDetail(asRecord(payload)))
      .catch((reason) => { if (!controller.signal.aborted) setDetailError(apiErrorText(reason)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedKey, Boolean(listedSelection), workspaceLoading]);

  const types = useMemo(() => [...new Set(pages.map((page) => page.type))].sort(), [pages]);
  const choose = (page: WikiPageCard) => {
    setSelectedKey(`${page.wikiId}:${page.path}`);
    setReaderOpen(true);
    history.replaceState(null, "", `${window.location.pathname}${window.location.search}${pageHash(page)}`);
    requestAnimationFrame(() => { readerRef.current?.focus(); readerRef.current?.scrollIntoView({ block: "start" }); });
  };

  const sources = asStringList(detail.sources);
  const outgoing = recordsFrom(detail.outgoing_links);
  const backlinks = recordsFrom(detail.backlinks);

  return <section className="page wiki-page" aria-labelledby="wiki-title">
    <PageHeader eyebrow="Knowledge Wiki" title="Background knowledge, connected." titleId="wiki-title" description="Search agent-maintained company, product, technology, science, industry, and value-chain knowledge. Pages are untrusted background material, not current investment evidence." action={<span className="page-count">{pages.length}<small>pages</small></span>} />
    <div className="wiki-toolbar">
      <label><span>Search</span><input type="search" value={query} placeholder="Search titles, aliases, tags, and content" onChange={(event) => setQuery(event.target.value)} /></label>
      <label><span>Wiki</span><select value={wiki} onChange={(event) => setWiki(event.target.value)}><option value="all">All active Wikis</option>{wikis.map((item) => <option key={item.wikiId} value={item.wikiId}>{item.wikiId}{item.version ? ` · ${item.version}` : ""}</option>)}</select></label>
      <label><span>Type</span><select value={type} onChange={(event) => setType(event.target.value)}><option value="">All types</option>{types.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
      <label><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{["draft", "current", "contested", "superseded"].map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
    </div>
    {listError && <ErrorNotice>{listError}</ErrorNotice>}
    <div className={`wiki-layout ${readerOpen ? "reader-open" : ""}`}>
      <aside className="wiki-index" ref={indexRef} tabIndex={-1} aria-label="Wiki folder tree and pages">
        <div className="index-heading"><span>Vault / pages</span><span>{listLoading ? "Scanning…" : `${pages.length} found`}</span></div>
        {listLoading && !pages.length ? <LoadingState label="Scanning active Wiki Markdown…" /> : pages.length ? <div className="wiki-page-list">{pages.map((page) => <button key={`${page.wikiId}:${page.path}`} type="button" className={`${page.wikiId}:${page.path}` === selectedKey ? "wiki-row selected" : "wiki-row"} onClick={() => choose(page)}><span className="wiki-folder">{page.wikiId} / {page.path.split("/").slice(1, -1).join(" / ") || "pages"}</span><strong>{page.title}</strong><p>{page.summary || page.aliases.join(" · ") || "No routing summary."}</p><span className="wiki-row-meta"><StatusPill value={page.status} /><span>{page.type} · {page.backlinkCount} backlinks</span></span></button>)}</div> : <EmptyState title="No Wiki pages found">Change the filters, or ask Codex explicitly to add reusable knowledge to the local Wiki.</EmptyState>}
      </aside>
      <article className="wiki-reader" ref={readerRef} tabIndex={-1} aria-busy={detailLoading}>
        <button className="mobile-back" type="button" onClick={() => { setReaderOpen(false); requestAnimationFrame(() => indexRef.current?.focus()); }}>← Back to pages</button>
        {!selected ? <EmptyState title="Choose a Wiki page">Select a page from the bounded local Markdown index.</EmptyState> : detailLoading ? <LoadingState label="Opening Wiki page…" /> : detailError ? <ErrorNotice>{detailError}</ErrorNotice> : <>
          <header className="reader-header"><div className="reader-kicker"><span>{selected.wikiId} · {selected.type}</span><StatusPill value={selected.status} /></div><h2>{asText(detail.title, selected.title)}</h2><p className="reader-summary">{asText(detail.summary, selected.summary)}</p><div className="wiki-trust-note">Untrusted background knowledge · verify current material facts through the Source Gate.</div></header>
          <dl className="reader-facts"><div><dt>Updated</dt><dd>{asText(detail.updated_at, "Not stated")}</dd></div><div><dt>Origin</dt><dd>{asText(detail.origin, "local")}</dd></div><div><dt>Sources</dt><dd>{sources.length}</dd></div><div><dt>Backlinks</dt><dd>{backlinks.length}</dd></div></dl>
          <div className="markdown-body wiki-markdown" dangerouslySetInnerHTML={{ __html: asText(detail.html) }} />
          <div className="wiki-links-grid"><section><h3>Sources</h3>{sources.length ? <ul>{sources.map((source) => <li key={source}>{source.startsWith("https://") ? <a href={source} target="_blank" rel="noreferrer">{source}</a> : <code>{source}</code>}</li>)}</ul> : <p>No sources listed.</p>}</section><section><h3>Outgoing links</h3>{outgoing.length ? <ul>{outgoing.map((link) => <li key={asText(link.target)}>{link.available === true ? <a href={pageHash({ wikiId: asText(link.wiki_id), path: asText(link.path) })}>{asText(link.title, asText(link.target))}</a> : asText(link.target)}</li>)}</ul> : <p>No outgoing links.</p>}</section><section><h3>Backlinks</h3>{backlinks.length ? <ul>{backlinks.map((link) => <li key={`${asText(link.wiki_id)}:${asText(link.path)}`}><a href={pageHash({ wikiId: asText(link.wiki_id), path: asText(link.path) })}>{asText(link.title)}</a></li>)}</ul> : <p>No backlinks.</p>}</section></div>
        </>}
      </article>
    </div>
    {workspaceLoading && <span className="sr-status">Workspace data is refreshing.</span>}
  </section>;
}
