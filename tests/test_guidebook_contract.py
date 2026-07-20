from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
GUIDEBOOK = ROOT / "guidebook"
HREF_RE = re.compile(r'href="([^"]+)"')
ID_RE = re.compile(r'id="([^"]+)"')
CANONICAL_GUIDE_ROUTES = [
    "index.html",
    "advanced.html",
    "dynamic-workflow.html",
    "harness.html",
    "data-sources.html",
    "decision-memory.html",
    "reusable-context.html",
    "execution-boundary.html",
    "provider-to-order.html",
    "skills.html",
    "research.html",
    "order.html",
    "improve.html",
    "customize.html",
    "help-status.html",
]


def _html(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_guidebook_local_routes_and_fragments_resolve() -> None:
    pages = sorted(GUIDEBOOK.glob("*.html"))
    known_fragments = {
        page.resolve(): set(ID_RE.findall(_html(page)))
        for page in pages
    }

    for page in pages:
        for raw_href in HREF_RE.findall(_html(page)):
            parsed = urlsplit(raw_href)
            if parsed.scheme or parsed.netloc or raw_href.startswith(("mailto:", "tel:")):
                continue
            target = page if not parsed.path else (page.parent / unquote(parsed.path)).resolve()
            assert target.exists(), f"{page.name}: missing route {raw_href}"
            if parsed.fragment and target.suffix == ".html":
                assert parsed.fragment in known_fragments[target], (
                    f"{page.name}: missing fragment {raw_href}"
                )


def test_provider_onboarding_uses_the_primary_guide_navigation() -> None:
    page = _html(GUIDEBOOK / "provider-to-order.html")
    header = page.split('<header class="global-header">', 1)[1].split("</header>", 1)[0]
    assert re.findall(r">([^<>]+)</a>", header)[-3:] == ["Guide", "Reference", "GitHub"]
    assert 'href="index.html" aria-current="page">Guide</a>' in header
    assert 'href="https://github.com/monarchjuno/tradingcodex/tree/main/docs">Reference</a>' in header


def test_every_page_uses_the_canonical_guide_sidebar() -> None:
    for path in sorted(GUIDEBOOK.glob("*.html")):
        sidebar = _html(path).split('<aside class="sidebar"', 1)[1].split(
            "</aside>", 1
        )[0]
        mobile = sidebar.split('<details class="mobile-nav">', 1)[1].split(
            "</details>", 1
        )[0]
        desktop = sidebar.split('<nav class="sidebar-nav">', 1)[1].split(
            "</nav>", 1
        )[0]
        assert HREF_RE.findall(mobile) == CANONICAL_GUIDE_ROUTES, path.name
        assert HREF_RE.findall(desktop) == CANONICAL_GUIDE_ROUTES, path.name


def test_full_desktop_shell_does_not_double_count_viewport_gutters() -> None:
    css = (GUIDEBOOK / "assets" / "site.css").read_text(encoding="utf-8")
    shell_rule = css.split(".docs-shell {", 1)[1].split("}", 1)[0]
    assert "max-width: 1460px" in shell_rule
    assert "padding: 2.75rem 1.5rem" in shell_rule
    assert "calc((100vw - 1460px) / 2)" not in shell_rule


def test_investor_context_guide_uses_direct_workspace_updates() -> None:
    page = _html(GUIDEBOOK / "skill-investor-context.html")

    assert "Codex updates the workspace-local Investor Context file directly" in page
    assert "need neither a Build turn nor a terminal command" in page
    assert "gives one exact user-terminal command" not in page
