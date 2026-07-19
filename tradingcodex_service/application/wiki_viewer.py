from __future__ import annotations

import hashlib
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote

from tradingcodex_service.application.knowledge_wikis import (
    LOCAL_WIKI_DIR,
    WIKI_VAULT_DIR,
    read_knowledge_wiki_records,
)
from tradingcodex_service.application.markdown_preview import (
    render_markdown_preview,
    split_markdown_frontmatter,
)


MAX_VIEWER_WIKIS = 64
MAX_VIEWER_PAGES = 4096
MAX_VIEWER_PAGE_BYTES = 512 * 1024
MAX_VIEWER_QUERY_LENGTH = 200
MAX_VIEWER_RESULT_LIMIT = 200
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")


@dataclass(frozen=True)
class WikiPageSource:
    wiki_id: str
    path: str
    origin: str
    file: Path
    markdown: str
    frontmatter: dict[str, Any]
    body: str
    outgoing_targets: tuple[str, ...]


def list_wiki_pages(
    workspace_root: Path | str,
    *,
    wiki: str = "all",
    query: str = "",
    page_type: str = "",
    status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    query = str(query or "").strip()
    if len(query) > MAX_VIEWER_QUERY_LENGTH:
        raise ValueError("wiki search query is too long")
    if type(limit) is not int or limit < 1 or limit > MAX_VIEWER_RESULT_LIMIT:
        raise ValueError(f"wiki result limit must be between 1 and {MAX_VIEWER_RESULT_LIMIT}")
    pages = _scan_pages(root)
    wiki_ids = _selected_wiki_ids(root)
    if wiki != "all":
        if wiki not in {str(item["wiki_id"]) for item in wiki_ids}:
            raise ValueError(f"unknown or inactive knowledge wiki: {wiki}")
        pages = {key: value for key, value in pages.items() if value.wiki_id == wiki}
    backlinks = _backlink_map(pages)
    needle = query.casefold()
    records: list[dict[str, Any]] = []
    for key, page in sorted(pages.items(), key=lambda item: (item[0][0], item[0][1])):
        metadata = page.frontmatter
        if page_type and str(metadata.get("type") or "") != page_type:
            continue
        if status and str(metadata.get("status") or "") != status:
            continue
        aliases = _string_list(metadata.get("aliases"))
        tags = _string_list(metadata.get("tags"))
        if needle and needle not in " ".join(
            (
                str(metadata.get("title") or ""),
                str(metadata.get("summary") or ""),
                *aliases,
                *tags,
                page.path,
                page.body,
            )
        ).casefold():
            continue
        records.append(_card(page, len(backlinks.get(key, set()))))
        if len(records) >= limit:
            break
    return {
        "pages": records,
        "wikis": wiki_ids,
        "filters": {
            "wiki": wiki,
            "q": query,
            "type": page_type,
            "status": status,
            "limit": limit,
        },
        "bounded": True,
        "read_only": True,
    }


def get_wiki_page_detail(
    workspace_root: Path | str,
    wiki_id: str,
    page_path: str,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    wiki_id = _validate_wiki_id(wiki_id)
    path = _normalize_page_path(page_path)
    pages = _scan_pages(root)
    page = pages.get((wiki_id, path))
    if page is None:
        raise ValueError(f"knowledge wiki page not found: {wiki_id}/{path}")
    backlinks = _backlink_map(pages)
    target_key = (wiki_id, path)
    outgoing = [
        _link_record(target, pages)
        for target in page.outgoing_targets
    ]
    incoming = [
        _card(pages[source], len(backlinks.get(source, set())))
        for source in sorted(backlinks.get(target_key, set()))
        if source in pages
    ]
    rendered = render_markdown_preview(
        _renderable_markdown(page.markdown),
        source_file=f"wikis/{wiki_id}/{path}",
        source_label="untrusted knowledge wiki page",
    )
    return {
        **_card(page, len(incoming)),
        "html": rendered.html,
        "sources": _string_list(page.frontmatter.get("sources")),
        "outgoing_links": outgoing,
        "backlinks": incoming,
        "content_hash": hashlib.sha256(page.markdown.encode("utf-8")).hexdigest(),
        "origin": page.origin,
        "content_safety": "untrusted-data",
        "read_only": True,
    }


def _selected_wiki_ids(root: Path) -> list[dict[str, Any]]:
    records = read_knowledge_wiki_records(root, include_removed=False)
    active = [record for record in records if record.get("active")]
    if len(active) > MAX_VIEWER_WIKIS - 1:
        raise ValueError("too many active knowledge wikis for the bounded viewer")
    return [
        {"wiki_id": "local", "origin": "local", "status": "active", "version": ""},
        *[
            {
                "wiki_id": record["wiki_id"],
                "origin": "community",
                "status": record["status"],
                "version": record["version"],
            }
            for record in active
        ],
    ]


def _scan_pages(root: Path) -> dict[tuple[str, str], WikiPageSource]:
    wikis = _selected_wiki_ids(root)
    pages: dict[tuple[str, str], WikiPageSource] = {}
    for wiki in wikis:
        wiki_id = str(wiki["wiki_id"])
        origin = str(wiki["origin"])
        wiki_root = root / (LOCAL_WIKI_DIR if wiki_id == "local" else WIKI_VAULT_DIR / wiki_id)
        page_root = wiki_root / "pages"
        if not page_root.is_dir() or page_root.is_symlink():
            continue
        for file in sorted(page_root.rglob("*.md")):
            if len(pages) >= MAX_VIEWER_PAGES:
                raise ValueError("knowledge wiki viewer page bound exceeded")
            if file.is_symlink() or not file.is_file():
                continue
            relative = file.relative_to(wiki_root).as_posix()
            _normalize_page_path(relative)
            try:
                info = file.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_VIEWER_PAGE_BYTES:
                    continue
                markdown = file.read_text(encoding="utf-8")
                document = split_markdown_frontmatter(markdown)
            except (OSError, UnicodeError, ValueError):
                continue
            targets = tuple(
                target
                for match in _WIKILINK.finditer(document.body)
                if (target := _canonical_target(match.group(1).strip()))
            )
            pages[(wiki_id, relative)] = WikiPageSource(
                wiki_id=wiki_id,
                path=relative,
                origin=origin,
                file=file,
                markdown=markdown,
                frontmatter=document.frontmatter,
                body=document.body,
                outgoing_targets=targets,
            )
    return pages


def _card(page: WikiPageSource, backlink_count: int) -> dict[str, Any]:
    metadata = page.frontmatter
    return {
        "wiki_id": page.wiki_id,
        "path": page.path,
        "title": str(metadata.get("title") or page.file.stem.replace("-", " ").title()),
        "summary": str(metadata.get("summary") or ""),
        "type": str(metadata.get("type") or "concept"),
        "status": str(metadata.get("status") or "draft"),
        "aliases": _string_list(metadata.get("aliases")),
        "tags": _string_list(metadata.get("tags")),
        "updated_at": str(metadata.get("updated_at") or ""),
        "source_count": len(_string_list(metadata.get("sources"))),
        "backlink_count": backlink_count,
    }


def _backlink_map(
    pages: dict[tuple[str, str], WikiPageSource],
) -> dict[tuple[str, str], set[tuple[str, str]]]:
    incoming: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for source, page in pages.items():
        for target in page.outgoing_targets:
            key = _target_key(target)
            if key in pages:
                incoming.setdefault(key, set()).add(source)
    return incoming


def _link_record(
    target: str,
    pages: dict[tuple[str, str], WikiPageSource],
) -> dict[str, Any]:
    key = _target_key(target)
    page = pages.get(key)
    return {
        "target": target,
        "wiki_id": key[0],
        "path": key[1],
        "title": str(page.frontmatter.get("title") or page.file.stem) if page else target,
        "available": page is not None,
    }


def _renderable_markdown(markdown: str) -> str:
    def replace(match: re.Match[str]) -> str:
        target = _canonical_target(match.group(1).strip())
        label = (match.group(2) or match.group(1)).strip().replace("[", "").replace("]", "")
        if not target:
            return label
        wiki_id, path = _target_key(target)
        href = f"#/wiki/{quote(wiki_id, safe='')}/{quote(path, safe='/')}"
        return f"[{label}]({href})"

    return _WIKILINK.sub(replace, markdown)


def _canonical_target(value: str) -> str:
    parts = PurePosixPath(value).parts
    if len(parts) < 3 or parts[1] != "pages":
        return ""
    wiki_id = parts[0]
    try:
        path = _normalize_page_path(PurePosixPath(*parts[1:]).as_posix())
        _validate_wiki_id(wiki_id)
    except ValueError:
        return ""
    return f"{wiki_id}/{path.removesuffix('.md')}"


def _target_key(target: str) -> tuple[str, str]:
    wiki_id, path = target.split("/", 1)
    return wiki_id, _normalize_page_path(path)


def _normalize_page_path(value: str) -> str:
    value = str(value or "").strip()
    if not value.endswith(".md"):
        value += ".md"
    path = PurePosixPath(value)
    if (
        not value.startswith("pages/")
        or path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("invalid knowledge wiki page path")
    return value


def _validate_wiki_id(value: str) -> str:
    if value == "local" or re.fullmatch(r"knowledge-wiki-[a-z0-9]+(?:-[a-z0-9]+)*", value):
        return value
    raise ValueError("invalid knowledge wiki id")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]
