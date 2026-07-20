import { useEffect, useMemo, useRef, useState } from "react";
import { Button, HTMLSelect, InputGroup } from "@blueprintjs/core";

import { apiErrorText, requestJSON } from "../api";
import { asRecord, asStringList, asText, normalizeWikiPage, recordsFrom, WikiPageCard } from "../domain";
import { EmptyState, ErrorNotice, LoadingState } from "../ui";

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

export function WikiPage() {
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
  }, [selectedKey, Boolean(listedSelection)]);

  const types = useMemo(() => [...new Set(pages.map((page) => page.type))].sort(), [pages]);
  const choose = (page: WikiPageCard) => {
    setSelectedKey(`${page.wikiId}:${page.path}`);
    setReaderOpen(true);
    history.replaceState(null, "", `${window.location.pathname}${window.location.search}${pageHash(page)}`);
    requestAnimationFrame(() => { readerRef.current?.scrollTo({ top: 0, behavior: "smooth" }); readerRef.current?.focus({ preventScroll: true }); });
  };

  const sources = asStringList(detail.sources);
  const backlinks = recordsFrom(detail.backlinks);

  return <section className="page wiki-page" aria-labelledby="wiki-title">
    <header className="wiki-appbar">
      <div className="wiki-title"><h1 id="wiki-title">Wiki</h1></div>
    </header>
    <div className="wiki-toolbar">
      <InputGroup aria-label="Search Wiki" leftIcon="search" type="search" value={query} placeholder="Search Wiki" onChange={(event) => setQuery(event.target.value)} />
      <HTMLSelect className="wiki-toolbar-wiki" aria-label="Vault" fill value={wiki} onChange={(event) => setWiki(event.target.value)}><option value="all">All Wikis</option>{wikis.map((item) => <option key={item.wikiId} value={item.wikiId}>{item.wikiId}{item.version ? ` · ${item.version}` : ""}</option>)}</HTMLSelect>
      <HTMLSelect aria-label="Type" fill value={type} onChange={(event) => setType(event.target.value)}><option value="">All types</option>{types.map((item) => <option key={item} value={item}>{item}</option>)}</HTMLSelect>
      <HTMLSelect aria-label="Status" fill value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{["draft", "current", "contested", "superseded"].map((item) => <option key={item} value={item}>{item}</option>)}</HTMLSelect>
    </div>
    {listError && <ErrorNotice>{listError}</ErrorNotice>}
    <div className={`wiki-layout ${readerOpen ? "reader-open" : ""}`}>
      <aside className="wiki-vault" aria-label="Knowledge vaults">
        <div className="wiki-pane-heading"><span>Vaults</span></div>
        <nav className="wiki-vault-list" aria-label="Select a knowledge vault">
          <Button active={wiki === "all"} alignText="left" fill minimal onClick={() => setWiki("all")} text="All notes" />
          {wikis.map((item) => <Button key={item.wikiId} active={wiki === item.wikiId} alignText="left" fill minimal onClick={() => setWiki(item.wikiId)} text={item.wikiId} />)}
        </nav>
      </aside>
      <aside className="wiki-index" ref={indexRef} tabIndex={-1} aria-label="Wiki folder tree and pages">
        <div className="wiki-pane-heading"><span>Notes</span></div>
        {listLoading && !pages.length ? <LoadingState label="Scanning Wiki…" /> : pages.length ? <div className="wiki-page-list">{pages.map((page) => <Button key={`${page.wikiId}:${page.path}`} active={`${page.wikiId}:${page.path}` === selectedKey} alignText="left" className="wiki-row" fill minimal aria-pressed={`${page.wikiId}:${page.path}` === selectedKey} onClick={() => choose(page)}><span className="wiki-row-copy"><strong>{page.title}</strong><span>{page.summary || page.aliases.join(" · ") || "No summary."}</span></span></Button>)}</div> : <EmptyState title="No Wiki pages found">Change the filters, or ask Codex explicitly to add reusable knowledge to the local Wiki.</EmptyState>}
      </aside>
      <article className="wiki-reader" ref={readerRef} tabIndex={-1} aria-busy={detailLoading}>
        <Button className="mobile-back" icon="arrow-left" minimal small onClick={() => { setReaderOpen(false); requestAnimationFrame(() => indexRef.current?.focus({ preventScroll: true })); }} text="Pages" />
        {!selected ? <EmptyState title="Choose a Wiki page">Select a page to read it.</EmptyState> : <>
          <header className="reader-header"><h2>{asText(detail.title, selected.title)}</h2><p className="reader-summary">{asText(detail.summary, selected.summary)}</p><p className="wiki-trust-note">Background knowledge only. Verify time-sensitive facts before acting.</p></header>
          {detailLoading ? <LoadingState compact label="Opening page…" /> : detailError ? <ErrorNotice>{detailError}</ErrorNotice> : <><div className="markdown-body wiki-markdown" dangerouslySetInnerHTML={{ __html: asText(detail.html) }} />{(sources.length > 0 || backlinks.length > 0) && <div className="wiki-links-grid">{sources.length > 0 && <section><h3>Sources</h3><ul>{sources.map((source) => <li key={source}>{source.startsWith("https://") ? <a href={source} target="_blank" rel="noreferrer">{source}</a> : <code>{source}</code>}</li>)}</ul></section>}{backlinks.length > 0 && <section><h3>Backlinks</h3><ul>{backlinks.map((link) => <li key={`${asText(link.wiki_id)}:${asText(link.path)}`}><a href={pageHash({ wikiId: asText(link.wiki_id), path: asText(link.path) })}>{asText(link.title)}</a></li>)}</ul></section>}</div>}</>}
        </>}
      </article>
    </div>
  </section>;
}
