from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
GUIDEBOOK = ROOT / "guidebook"
HREF_RE = re.compile(r'href="([^"]+)"')
ID_RE = re.compile(r'id="([^"]+)"')


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


def test_provider_mobile_menu_reaches_every_guide_section() -> None:
    page = _html(GUIDEBOOK / "provider-to-order.html")
    mobile = page.split('<details class="mobile-nav">', 1)[1].split("</details>", 1)[0]
    assert HREF_RE.findall(mobile) == [
        "index.html",
        "dynamic-workflow.html",
        "harness.html",
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


def test_full_desktop_shell_does_not_double_count_viewport_gutters() -> None:
    css = (GUIDEBOOK / "assets" / "site.css").read_text(encoding="utf-8")
    shell_rule = css.split(".docs-shell {", 1)[1].split("}", 1)[0]
    assert "max-width: 1460px" in shell_rule
    assert "padding: 2.75rem 1.5rem" in shell_rule
    assert "calc((100vw - 1460px) / 2)" not in shell_rule
