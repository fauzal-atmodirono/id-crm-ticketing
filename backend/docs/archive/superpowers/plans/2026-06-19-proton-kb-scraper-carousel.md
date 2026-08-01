# Rich Proton KB Scraper + Product Carousel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shallow Proton scraper with a modular, JS-aware scraper that puts rich per-model content (description, price, images, brochure text, specs) into Vertex `struct_data`, then add an additive product carousel to the chat UI — eliminating the "I don't have detailed descriptions" apology.

**Architecture:** Phase 1 builds a new `apps/backend/src/scraper/` package (ported from the owner's CelcomDigi scraper): hybrid httpx+Selenium transport, URL classifier, per-type extractors, local brochure PDF text extraction, and an idempotent Vertex Discovery Engine indexer — all writing enriched `struct_data` to the existing `proton-kb` data store on Standard tier. Phase 2 adds an A2UI-shaped `product_carousel`: a new ADK agent tool that stores model cards in tool-context state, surfaced through `TurnResult`/`ChatTurnResponse` and rendered by a new Vue `ProductCarousel.vue`.

**Tech Stack:** Python 3.12, FastAPI, Google ADK (Gemini), `google-cloud-discoveryengine`, `google-cloud-storage`, BeautifulSoup, Selenium + webdriver-manager, pypdf; Vue 3 + Pinia + TypeScript (Vite), `marked` + DOMPurify.

## Global Constraints

- **Zendesk is off-limits.** Never modify `apps/backend/src/chatbot/features/chat/adapters/zendesk.py`, the `TicketingPort`, or the handoff/escalation paths in `service.py` (`_escalate_handoff`, `_open_live_bridge`, the Sunshine bridge) or `emit_handoff_tool`. All work is additive.
- **Vertex stays on Standard tier.** It returns only `struct_data` — never content snippets. All agent-readable text (including brochure PDF text) goes into `struct_data`.
- Reuse existing GCP resources verbatim: project `lv-playground-genai`, location `global`, data store `proton-kb`, engine `proton-kb-engine`, bucket `proton-kb-scraped-lv-playground-genai`.
- Backend: `requires-python = ">=3.12"`; ruff line-length 100; mypy `--strict`. Run from `apps/backend/` with `.venv/bin/<tool>`.
- Frontend: `vue-tsc` strict. No test runner exists — verify with `npm run type-check` + `npm run build`. Do not add vitest.
- Branch: `feat/proton-kb-scraper-carousel` (already created; spec committed there).
- Commits: conventional format; end body with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- The new package lives at `apps/backend/src/scraper/`. `pytest` config already sets `pythonpath = ["src"]`, so `import scraper...` works in tests without packaging changes.

---

# PHASE 1 — Data layer (scraper + enriched KB)

> **Provisioning is intentionally out of scope.** The spec mentions `provisioner.py` /
> a `provision` command for idempotent data-store/engine creation, but the
> `proton-kb` data store and `proton-kb-engine` engine **already exist** and are
> reused verbatim (Global Constraints). The CLI ships only `scrape` and `index`.
> If a fresh environment ever needs provisioning, port `ensure_data_store_and_engine`
> from the legacy `scripts/scrape_proton.py` (kept until Task 11) into a new
> `provisioner.py` then — not now (YAGNI).

## Task 1: Scaffold scraper package — dependencies, config, models

**Files:**
- Modify: `apps/backend/pyproject.toml:9-24` (add deps)
- Create: `apps/backend/src/scraper/__init__.py`
- Create: `apps/backend/src/scraper/config.py`
- Create: `apps/backend/src/scraper/models.py`
- Create: `apps/backend/src/scraper/tests/__init__.py`
- Test: `apps/backend/src/scraper/tests/test_models.py`

**Interfaces:**
- Produces: `ScraperSettings` (pydantic-settings, env prefix `SCRAPER_`); `ScrapedDoc` dataclass with `to_document() -> dict[str, object]`.

- [ ] **Step 1: Add dependencies**

In `apps/backend/pyproject.toml`, append to the `dependencies` list (after line 23 `google-cloud-storage>=3.12.0`):

```toml
  "selenium>=4.20",
  "webdriver-manager>=4.0",
  "pypdf>=5.0",
```

- [ ] **Step 2: Install**

Run: `cd apps/backend && uv sync`
Expected: resolves and installs selenium, webdriver-manager, pypdf.

- [ ] **Step 3: Create package init files**

`apps/backend/src/scraper/__init__.py`:
```python
"""Modular Proton website scraper → Vertex AI Search ingest pipeline."""
```

`apps/backend/src/scraper/tests/__init__.py`:
```python
```

- [ ] **Step 4: Create config**

`apps/backend/src/scraper/config.py`:
```python
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ScraperSettings(BaseSettings):
    """Scraper configuration. Override any field via SCRAPER_* env vars."""

    project_id: str = "lv-playground-genai"
    location: str = "global"
    data_store_id: str = "proton-kb"
    engine_id: str = "proton-kb-engine"
    bucket_name: str = "proton-kb-scraped-lv-playground-genai"
    gcs_prefix: str = "scraped_data"
    sitemap_url: str = "https://www.proton.com/sitemap.xml"

    # Crawl behaviour
    request_timeout: float = 20.0
    delay_seconds: float = 1.0
    headless: bool = True
    # Render with Selenium only when httpx content looks empty.
    min_main_chars: int = 200

    # Only crawl these path prefixes; drop everything else.
    allow_prefixes: tuple[str, ...] = ("/models", "/all-new", "/after-sales", "/ownership")
    deny_substrings: tuple[str, ...] = ("/privacy", "/thank-you", "/cookie", "/terms")

    model_config = SettingsConfigDict(env_prefix="SCRAPER_", extra="ignore")
```

- [ ] **Step 5: Write the failing test for models**

`apps/backend/src/scraper/tests/test_models.py`:
```python
from __future__ import annotations

from scraper.models import ScrapedDoc


def test_to_document_packs_struct_data_and_content_uri() -> None:
    doc = ScrapedDoc(
        doc_id="models_all-new-x50",
        title="PROTON All-New X50",
        link="https://www.proton.com/models/all-new-x50",
        source_type="model",
        language="en",
        body="Full cleaned description text.",
        price="RM 89,800",
        image_urls=["https://img/x50.jpg"],
        brochure_url="https://img/x50.pdf",
        sections=["Stylish Design"],
        gcs_uri="gs://bucket/scraped_data/models_all-new-x50.html",
    )
    out = doc.to_document()
    assert out["id"] == "models_all-new-x50"
    sd = out["struct_data"]
    assert sd["title"] == "PROTON All-New X50"
    assert sd["source_type"] == "model"
    assert sd["price"] == "RM 89,800"
    assert sd["body_excerpt"] == "Full cleaned description text."
    assert sd["image_urls"] == ["https://img/x50.jpg"]
    assert sd["brochure_url"] == "https://img/x50.pdf"
    assert out["content"]["uri"] == "gs://bucket/scraped_data/models_all-new-x50.html"


def test_to_document_truncates_body_utf8_safe() -> None:
    doc = ScrapedDoc(
        doc_id="d",
        title="t",
        link="u",
        source_type="generic",
        language="en",
        body="é" * 100_000,  # multi-byte, exceeds cap
        gcs_uri="gs://b/x.html",
    )
    out = doc.to_document()
    body = out["struct_data"]["body_excerpt"]
    assert isinstance(body, str)
    # Encodes cleanly (no partial multi-byte char) and is bounded.
    assert len(body.encode("utf-8")) <= ScrapedDoc.STRUCT_BODY_CAP_BYTES
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.models'`.

- [ ] **Step 7: Create models**

`apps/backend/src/scraper/models.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SourceType = Literal["model", "after_sales", "offer", "generic", "brochure"]


@dataclass(frozen=True)
class ScrapedDoc:
    """Canonical scraped record → one Vertex Discovery Engine document."""

    doc_id: str
    title: str
    link: str
    source_type: SourceType
    language: str
    body: str
    gcs_uri: str
    price: str | None = None
    image_urls: list[str] = field(default_factory=list)
    brochure_url: str | None = None
    sections: list[str] = field(default_factory=list)

    # struct_data is returnable on Standard tier; keep it under Vertex's limit.
    STRUCT_BODY_CAP_BYTES = 60_000

    def _capped_body(self) -> str:
        encoded = self.body.encode("utf-8")
        if len(encoded) <= self.STRUCT_BODY_CAP_BYTES:
            return self.body
        # Truncate at a UTF-8 boundary.
        return encoded[: self.STRUCT_BODY_CAP_BYTES].decode("utf-8", errors="ignore")

    def to_document(self) -> dict[str, object]:
        struct: dict[str, object] = {
            "title": self.title,
            "link": self.link,
            "source_type": self.source_type,
            "language": self.language,
            "body_excerpt": self._capped_body(),
            "sections": self.sections,
            "image_urls": self.image_urls,
        }
        if self.price is not None:
            struct["price"] = self.price
        if self.brochure_url is not None:
            struct["brochure_url"] = self.brochure_url
        return {
            "id": self.doc_id,
            "struct_data": struct,
            "content": {"uri": self.gcs_uri, "mime_type": "text/html"},
        }
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_models.py -v`
Expected: PASS (2 passed).

- [ ] **Step 9: Lint + type-check**

Run: `cd apps/backend && .venv/bin/ruff check src/scraper && .venv/bin/mypy src/scraper --strict`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/src/scraper
git commit -m "feat(scraper): scaffold package with config + ScrapedDoc model

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: URL classifier

**Files:**
- Create: `apps/backend/src/scraper/classifier.py`
- Test: `apps/backend/src/scraper/tests/test_classifier.py`

**Interfaces:**
- Produces: `classify(url: str) -> SourceType`.

- [ ] **Step 1: Write the failing test**

`apps/backend/src/scraper/tests/test_classifier.py`:
```python
from __future__ import annotations

import pytest

from scraper.classifier import classify


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.proton.com/models/all-new-x50", "model"),
        ("https://www.proton.com/all-new-saga", "model"),
        ("https://www.proton.com/after-sales/owners-manual/x50", "after_sales"),
        ("https://www.proton.com/ownership/offers", "offer"),
        ("https://www.proton.com/about-us", "generic"),
    ],
)
def test_classify(url: str, expected: str) -> None:
    assert classify(url) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_classifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.classifier'`.

- [ ] **Step 3: Write implementation**

`apps/backend/src/scraper/classifier.py`:
```python
from __future__ import annotations

from urllib.parse import urlparse

from scraper.models import SourceType


def classify(url: str) -> SourceType:
    path = urlparse(url).path.rstrip("/")
    if path.startswith("/models") or path.startswith("/all-new"):
        return "model"
    if path.startswith("/after-sales"):
        return "after_sales"
    if "offer" in path or path.startswith("/ownership"):
        return "offer"
    return "generic"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_classifier.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint + type-check + commit**

```bash
cd apps/backend && .venv/bin/ruff check src/scraper && .venv/bin/mypy src/scraper --strict
git add apps/backend/src/scraper/classifier.py apps/backend/src/scraper/tests/test_classifier.py
git commit -m "feat(scraper): URL page-type classifier

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: HTML utilities + generic extractor (chrome stripping)

**Files:**
- Create: `apps/backend/src/scraper/html_utils.py`
- Create: `apps/backend/src/scraper/extractors/__init__.py`
- Create: `apps/backend/src/scraper/extractors/generic.py`
- Create: `apps/backend/src/scraper/tests/fixtures/generic_about.html`
- Test: `apps/backend/src/scraper/tests/test_generic_extractor.py`

**Interfaces:**
- Produces: `clean_text(s: str) -> str`; `main_text(soup_or_html: str) -> str` (selector-based chrome strip); `extract_generic(html: str, url: str) -> ScrapedDoc` (with `gcs_uri=""`, filled by indexer stage).

- [ ] **Step 1: Create the fixture**

`apps/backend/src/scraper/tests/fixtures/generic_about.html`:
```html
<!DOCTYPE html>
<html lang="en">
<head><title>PROTON - About Us</title></head>
<body>
  <nav>Models Back MODELS Starting at RM89,800 TEST DRIVE Log in</nav>
  <header>Site header junk</header>
  <main>
    <h1>About PROTON</h1>
    <p>PROTON is a Malaysian automotive company founded in 1983.</p>
    <h2>Our Mission</h2>
    <p>To build cars Malaysians are proud of.</p>
  </main>
  <footer>Footer links and copyright</footer>
  <script>var x = 1;</script>
</body>
</html>
```

- [ ] **Step 2: Write the failing test**

`apps/backend/src/scraper/tests/test_generic_extractor.py`:
```python
from __future__ import annotations

from pathlib import Path

from scraper.extractors.generic import extract_generic

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_generic_strips_chrome_and_keeps_main() -> None:
    html = (FIXTURES / "generic_about.html").read_text(encoding="utf-8")
    doc = extract_generic(html, "https://www.proton.com/about-us")

    assert doc.title == "PROTON - About Us"
    assert doc.source_type == "generic"
    assert doc.link == "https://www.proton.com/about-us"
    # Main content survives.
    assert "Malaysian automotive company" in doc.body
    assert "To build cars Malaysians are proud of." in doc.body
    # Nav/footer/script chrome is gone.
    assert "TEST DRIVE" not in doc.body
    assert "Footer links" not in doc.body
    assert "var x" not in doc.body
    assert doc.sections == ["About PROTON", "Our Mission"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_generic_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.extractors'`.

- [ ] **Step 4: Write html_utils**

`apps/backend/src/scraper/html_utils.py`:
```python
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

_CHROME_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "iframe", "svg"]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def page_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return "PROTON Page"


def page_language(soup: BeautifulSoup) -> str:
    lang = (soup.html.get("lang") if soup.html else None) or "en"
    lang = str(lang).split("-")[0].lower()
    return lang if lang in {"en", "ms", "zh"} else "en"


def main_container(soup: BeautifulSoup) -> Tag:
    """Pick the richest content container, falling back to <body>."""
    for selector in ("main", "article", "div.content", "div#content"):
        node = soup.select_one(selector)
        if node is not None:
            return node
    return soup.body or soup


def strip_chrome(node: Tag) -> None:
    for el in node(_CHROME_TAGS):
        el.decompose()


def headings(node: Tag, limit: int = 20) -> list[str]:
    out: list[str] = []
    for h in node.find_all(["h1", "h2", "h3"]):
        t = clean_text(h.get_text())
        if t and t not in out:
            out.append(t)
        if len(out) >= limit:
            break
    return out
```

- [ ] **Step 5: Write the generic extractor**

`apps/backend/src/scraper/extractors/__init__.py`:
```python
```

`apps/backend/src/scraper/extractors/generic.py`:
```python
from __future__ import annotations

from bs4 import BeautifulSoup

from scraper.classifier import classify
from scraper.html_utils import (
    clean_text,
    headings,
    main_container,
    page_language,
    page_title,
    strip_chrome,
)
from scraper.models import ScrapedDoc


def extract_generic(html: str, url: str) -> ScrapedDoc:
    soup = BeautifulSoup(html, "html.parser")
    title = page_title(soup)
    lang = page_language(soup)
    main = main_container(soup)
    # Capture headings BEFORE stripping (headings live inside main, not chrome).
    sections = headings(main)
    strip_chrome(main)
    body = clean_text(main.get_text(separator=" "))
    return ScrapedDoc(
        doc_id="",  # assigned by the pipeline (slug)
        title=title,
        link=url,
        source_type=classify(url),
        language=lang,
        body=body,
        gcs_uri="",
        sections=sections,
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_generic_extractor.py -v`
Expected: PASS (1 passed).

- [ ] **Step 7: Lint + type-check + commit**

```bash
cd apps/backend && .venv/bin/ruff check src/scraper && .venv/bin/mypy src/scraper --strict
git add apps/backend/src/scraper/html_utils.py apps/backend/src/scraper/extractors apps/backend/src/scraper/tests/fixtures/generic_about.html apps/backend/src/scraper/tests/test_generic_extractor.py
git commit -m "feat(scraper): html utils + generic extractor with chrome stripping

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Model extractor (price, images, brochure link, specs)

**Files:**
- Create: `apps/backend/src/scraper/extractors/model.py`
- Create: `apps/backend/src/scraper/tests/fixtures/model_x50.html`
- Test: `apps/backend/src/scraper/tests/test_model_extractor.py`

**Interfaces:**
- Consumes: `html_utils`, `ScrapedDoc`.
- Produces: `extract_model(html: str, url: str) -> ScrapedDoc` with `source_type="model"`, populated `price`, `image_urls`, `brochure_url`, `sections`.

- [ ] **Step 1: Create the fixture**

`apps/backend/src/scraper/tests/fixtures/model_x50.html`:
```html
<!DOCTYPE html>
<html lang="en">
<head><title>PROTON - PROTON All-New X50</title></head>
<body>
  <nav>Models Back MODELS Starting at RM89,800 TEST DRIVE</nav>
  <main>
    <h1>All-New X50</h1>
    <p class="price">Starting at RM 89,800</p>
    <p>The All-New X50 is a compact SUV with stylish dual-tone design,
       full LED headlamps, and 18-inch alloy wheels.</p>
    <img src="/images/x50-front.jpg" alt="X50 front">
    <img src="https://cdn.proton.com/x50-interior.jpg" alt="X50 interior">
    <img src="/images/icon-arrow.svg" alt="">
    <h2>Specifications</h2>
    <a href="/downloads/x50-brochure.pdf">Download Brochure</a>
  </main>
</body>
</html>
```

- [ ] **Step 2: Write the failing test**

`apps/backend/src/scraper/tests/test_model_extractor.py`:
```python
from __future__ import annotations

from pathlib import Path

from scraper.extractors.model import extract_model

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.proton.com/models/all-new-x50"


def test_extract_model_fields() -> None:
    html = (FIXTURES / "model_x50.html").read_text(encoding="utf-8")
    doc = extract_model(html, URL)

    assert doc.source_type == "model"
    assert doc.title == "PROTON - PROTON All-New X50"
    assert doc.price == "RM 89,800"
    assert "compact SUV" in doc.body
    # Relative image URLs resolved to absolute; .svg icons excluded.
    assert "https://www.proton.com/images/x50-front.jpg" in doc.image_urls
    assert "https://cdn.proton.com/x50-interior.jpg" in doc.image_urls
    assert all(not u.endswith(".svg") for u in doc.image_urls)
    # Brochure link resolved absolute.
    assert doc.brochure_url == "https://www.proton.com/downloads/x50-brochure.pdf"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_model_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.extractors.model'`.

- [ ] **Step 4: Write the model extractor**

`apps/backend/src/scraper/extractors/model.py`:
```python
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper.html_utils import (
    clean_text,
    headings,
    main_container,
    page_language,
    page_title,
    strip_chrome,
)
from scraper.models import ScrapedDoc

_PRICE_RE = re.compile(r"RM\s?[\d,]+(?:\.\d{2})?")


def _find_price(text: str) -> str | None:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    # Normalize "RM89,800" / "RM 89,800" → "RM 89,800".
    raw = m.group(0).replace("RM", "RM ").replace("  ", " ")
    return clean_text(raw)


def _image_urls(node: BeautifulSoup, base_url: str) -> list[str]:
    out: list[str] = []
    for img in node.find_all("img"):
        src = img.get("src")
        if not src or src.endswith(".svg"):
            continue
        absolute = urljoin(base_url, src)
        if absolute not in out:
            out.append(absolute)
    return out


def _brochure_url(node: BeautifulSoup, base_url: str) -> str | None:
    for a in node.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            return urljoin(base_url, href)
    return None


def extract_model(html: str, url: str) -> ScrapedDoc:
    soup = BeautifulSoup(html, "html.parser")
    title = page_title(soup)
    lang = page_language(soup)
    main = main_container(soup)

    sections = headings(main)
    images = _image_urls(main, url)
    brochure = _brochure_url(main, url)

    # Derive price from raw text before chrome removal can drop it.
    price = _find_price(main.get_text(separator=" "))

    strip_chrome(main)
    body = clean_text(main.get_text(separator=" "))

    return ScrapedDoc(
        doc_id="",
        title=title,
        link=url,
        source_type="model",
        language=lang,
        body=body,
        gcs_uri="",
        price=price,
        image_urls=images,
        brochure_url=brochure,
        sections=sections,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_model_extractor.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Lint + type-check + commit**

```bash
cd apps/backend && .venv/bin/ruff check src/scraper && .venv/bin/mypy src/scraper --strict
git add apps/backend/src/scraper/extractors/model.py apps/backend/src/scraper/tests/fixtures/model_x50.html apps/backend/src/scraper/tests/test_model_extractor.py
git commit -m "feat(scraper): model extractor for price, images, brochure link

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Brochure PDF text extraction

**Files:**
- Create: `apps/backend/src/scraper/pdf.py`
- Test: `apps/backend/src/scraper/tests/test_pdf.py`

**Interfaces:**
- Produces: `extract_pdf_text(data: bytes) -> str` (returns `""` on failure, never raises).

- [ ] **Step 1: Write the failing test**

`apps/backend/src/scraper/tests/test_pdf.py`:
```python
from __future__ import annotations

import io

from pypdf import PdfWriter

from scraper.pdf import extract_pdf_text


def _blank_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_pdf_text_handles_valid_pdf() -> None:
    # A blank page has no text but must not raise; returns a string.
    result = extract_pdf_text(_blank_pdf_bytes())
    assert isinstance(result, str)


def test_extract_pdf_text_returns_empty_on_garbage() -> None:
    assert extract_pdf_text(b"not a pdf") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_pdf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.pdf'`.

- [ ] **Step 3: Write implementation**

`apps/backend/src/scraper/pdf.py`:
```python
from __future__ import annotations

import io

import structlog
from pypdf import PdfReader

from scraper.html_utils import clean_text

_log = structlog.get_logger(__name__)


def extract_pdf_text(data: bytes) -> str:
    """Extract plain text from PDF bytes. Returns '' on any failure."""
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = [page.extract_text() or "" for page in reader.pages]
        return clean_text(" ".join(parts))
    except Exception as e:  # noqa: BLE001 - scraper must never crash on a bad PDF
        _log.warning("pdf_extract_failed", error=str(e))
        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_pdf.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint + type-check + commit**

```bash
cd apps/backend && .venv/bin/ruff check src/scraper && .venv/bin/mypy src/scraper --strict
git add apps/backend/src/scraper/pdf.py apps/backend/src/scraper/tests/test_pdf.py
git commit -m "feat(scraper): brochure PDF text extraction

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Sitemap fetch + filter

**Files:**
- Create: `apps/backend/src/scraper/sitemap.py`
- Test: `apps/backend/src/scraper/tests/test_sitemap.py`

**Interfaces:**
- Consumes: `ScraperSettings`.
- Produces: `filter_urls(urls: list[str], settings: ScraperSettings) -> list[str]`; `parse_sitemap(xml: bytes) -> list[str]`.

- [ ] **Step 1: Write the failing test**

`apps/backend/src/scraper/tests/test_sitemap.py`:
```python
from __future__ import annotations

from scraper.config import ScraperSettings
from scraper.sitemap import filter_urls, parse_sitemap

SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.proton.com/models/all-new-x50</loc></url>
  <url><loc>https://www.proton.com/privacy-notice</loc></url>
  <url><loc>https://www.proton.com/about-us</loc></url>
</urlset>"""


def test_parse_sitemap_extracts_locs() -> None:
    urls = parse_sitemap(SITEMAP)
    assert "https://www.proton.com/models/all-new-x50" in urls
    assert len(urls) == 3


def test_filter_urls_applies_allow_and_deny() -> None:
    settings = ScraperSettings()
    urls = parse_sitemap(SITEMAP)
    kept = filter_urls(urls, settings)
    assert "https://www.proton.com/models/all-new-x50" in kept
    # Denied by substring.
    assert "https://www.proton.com/privacy-notice" not in kept
    # Not in allow_prefixes.
    assert "https://www.proton.com/about-us" not in kept
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_sitemap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.sitemap'`.

- [ ] **Step 3: Write implementation**

`apps/backend/src/scraper/sitemap.py`:
```python
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from scraper.config import ScraperSettings


def parse_sitemap(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)
    return [e.text for e in root.iter() if e.tag.endswith("loc") and e.text]


def filter_urls(urls: list[str], settings: ScraperSettings) -> list[str]:
    kept: list[str] = []
    for url in urls:
        if any(s in url for s in settings.deny_substrings):
            continue
        path = urlparse(url).path
        if not any(path.startswith(p) for p in settings.allow_prefixes):
            continue
        if url not in kept:
            kept.append(url)
    return sorted(kept)


def fetch_sitemap_urls(settings: ScraperSettings) -> list[str]:
    req = urllib.request.Request(  # noqa: S310 - fixed https sitemap URL
        settings.sitemap_url, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        xml = resp.read()
    return filter_urls(parse_sitemap(xml), settings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_sitemap.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint + type-check + commit**

```bash
cd apps/backend && .venv/bin/ruff check src/scraper && .venv/bin/mypy src/scraper --strict
git add apps/backend/src/scraper/sitemap.py apps/backend/src/scraper/tests/test_sitemap.py
git commit -m "feat(scraper): sitemap fetch + allow/deny filtering

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Hybrid transport (httpx + Selenium fallback)

**Files:**
- Create: `apps/backend/src/scraper/transport.py`
- Test: `apps/backend/src/scraper/tests/test_transport.py`

**Interfaces:**
- Consumes: `ScraperSettings`, `html_utils.main_container`.
- Produces: `needs_render(html: str, settings: ScraperSettings) -> bool`; `Transport` class with `fetch(url) -> str | None` (httpx first, Selenium fallback) and `close()`. Selenium import is lazy so unit tests never load a browser.

- [ ] **Step 1: Write the failing test**

`apps/backend/src/scraper/tests/test_transport.py`:
```python
from __future__ import annotations

from scraper.config import ScraperSettings
from scraper.transport import needs_render

SETTINGS = ScraperSettings()


def test_needs_render_true_for_empty_main() -> None:
    html = "<html><body><main></main></body></html>"
    assert needs_render(html, SETTINGS) is True


def test_needs_render_false_for_rich_main() -> None:
    html = "<html><body><main>" + ("content " * 100) + "</main></body></html>"
    assert needs_render(html, SETTINGS) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_transport.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.transport'`.

- [ ] **Step 3: Write implementation**

`apps/backend/src/scraper/transport.py`:
```python
from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from bs4 import BeautifulSoup

from scraper.config import ScraperSettings
from scraper.html_utils import main_container

_log = structlog.get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def needs_render(html: str, settings: ScraperSettings) -> bool:
    """True when the static HTML's main content is too thin (JS-rendered page)."""
    soup = BeautifulSoup(html, "html.parser")
    text = main_container(soup).get_text(separator=" ", strip=True)
    return len(text) < settings.min_main_chars


class Transport:
    """httpx-first fetcher with a lazily-created Selenium fallback."""

    def __init__(self, settings: ScraperSettings) -> None:
        self._settings = settings
        self._client = httpx.Client(
            headers={"User-Agent": _UA},
            timeout=settings.request_timeout,
            follow_redirects=True,
        )
        self._driver: Any | None = None

    def _selenium_driver(self) -> Any:
        if self._driver is None:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from webdriver_manager.chrome import ChromeDriverManager  # noqa: PLC0415
            from selenium.webdriver.chrome.service import Service  # noqa: PLC0415

            opts = Options()
            if self._settings.headless:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument(f"user-agent={_UA}")
            self._driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()), options=opts
            )
        return self._driver

    def fetch(self, url: str) -> str | None:
        time.sleep(self._settings.delay_seconds)
        try:
            res = self._client.get(url)
            if res.status_code != 200:
                _log.info("fetch_non_200", url=url, status=res.status_code)
                return None
            html = res.text
        except Exception as e:  # noqa: BLE001
            _log.warning("httpx_fetch_failed", url=url, error=str(e))
            return None

        if not needs_render(html, self._settings):
            return html

        # Fallback: render with Selenium.
        try:
            driver = self._selenium_driver()
            driver.get(url)
            time.sleep(2.0)
            return str(driver.page_source)
        except Exception as e:  # noqa: BLE001
            _log.warning("selenium_fetch_failed", url=url, error=str(e))
            return html  # better than nothing

    def close(self) -> None:
        self._client.close()
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception as e:  # noqa: BLE001
                _log.warning("selenium_quit_failed", error=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_transport.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint + type-check + commit**

```bash
cd apps/backend && .venv/bin/ruff check src/scraper && .venv/bin/mypy src/scraper --strict
git add apps/backend/src/scraper/transport.py apps/backend/src/scraper/tests/test_transport.py
git commit -m "feat(scraper): hybrid httpx + Selenium-fallback transport

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Indexer (build documents + GCS upload + import)

**Files:**
- Create: `apps/backend/src/scraper/indexer.py`
- Test: `apps/backend/src/scraper/tests/test_indexer.py`

**Interfaces:**
- Consumes: `ScrapedDoc`, `ScraperSettings`.
- Produces: `slug_for(url) -> str`; `doc_id_for(slug) -> str`; `write_jsonl(docs, path) -> None`; `import_documents(settings)` and `upload_jsonl(settings, path)` (network — not unit-tested here, exercised in Task 10).

- [ ] **Step 1: Write the failing test**

`apps/backend/src/scraper/tests/test_indexer.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

from scraper.indexer import doc_id_for, slug_for, write_jsonl
from scraper.models import ScrapedDoc


def test_slug_and_doc_id() -> None:
    slug = slug_for("https://www.proton.com/models/all-new-x50/")
    assert slug == "www.proton.com_models_all-new-x50"
    assert doc_id_for(slug) == "www_proton_com_models_all-new-x50"[:63]


def test_write_jsonl(tmp_path: Path) -> None:
    docs = [
        ScrapedDoc(
            doc_id="d1", title="t", link="u", source_type="model",
            language="en", body="b", gcs_uri="gs://x/d1.html", price="RM 1",
        )
    ]
    out = tmp_path / "out.jsonl"
    write_jsonl(docs, out)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["id"] == "d1"
    assert rec["struct_data"]["price"] == "RM 1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_indexer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.indexer'`.

- [ ] **Step 3: Write implementation**

`apps/backend/src/scraper/indexer.py`:
```python
from __future__ import annotations

import json
import re
from pathlib import Path

import structlog
from google.cloud import discoveryengine_v1beta as discoveryengine
from google.cloud import storage

from scraper.config import ScraperSettings
from scraper.models import ScrapedDoc

_log = structlog.get_logger(__name__)


def slug_for(url: str) -> str:
    bare = url.replace("https://", "").replace("http://", "").rstrip("/")
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", bare) or "index"


def doc_id_for(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", slug)[:63] or "doc"


def write_jsonl(docs: list[ScrapedDoc], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(d.to_document(), ensure_ascii=False) for d in docs]
    path.write_text("\n".join(lines), encoding="utf-8")
    _log.info("wrote_jsonl", count=len(docs), path=str(path))


def upload_jsonl(settings: ScraperSettings, path: Path) -> str:
    client = storage.Client(project=settings.project_id)
    bucket = client.bucket(settings.bucket_name)
    blob_name = f"{settings.gcs_prefix}/proton-kb.jsonl"
    bucket.blob(blob_name).upload_from_filename(str(path), content_type="application/json")
    gcs_uri = f"gs://{settings.bucket_name}/{blob_name}"
    _log.info("uploaded_jsonl", uri=gcs_uri)
    return gcs_uri


def import_documents(settings: ScraperSettings, gcs_uri: str) -> None:
    client = discoveryengine.DocumentServiceClient()
    parent = client.branch_path(
        settings.project_id, settings.location, settings.data_store_id, "default_branch"
    )
    request = discoveryengine.ImportDocumentsRequest(
        parent=parent,
        gcs_source=discoveryengine.GcsSource(input_uris=[gcs_uri], data_schema="document"),
        reconciliation_mode=discoveryengine.ImportDocumentsRequest.ReconciliationMode.FULL,
    )
    op = client.import_documents(request=request)
    _log.info("import_started", lro=op.operation.name)
    op.result()
    _log.info("import_completed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_indexer.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint + type-check + commit**

```bash
cd apps/backend && .venv/bin/ruff check src/scraper && .venv/bin/mypy src/scraper --strict
git add apps/backend/src/scraper/indexer.py apps/backend/src/scraper/tests/test_indexer.py
git commit -m "feat(scraper): JSONL builder + GCS upload + Vertex import

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Pipeline orchestration + CLI

**Files:**
- Create: `apps/backend/src/scraper/pipeline.py`
- Create: `apps/backend/src/scraper/main.py`
- Test: `apps/backend/src/scraper/tests/test_pipeline.py`

**Interfaces:**
- Consumes: all prior modules.
- Produces: `build_doc(url, html, settings, transport) -> ScrapedDoc` (dispatch by classifier; model pages fetch+extract brochure text and append it to `body`); `run_scrape(settings, urls, transport) -> list[ScrapedDoc]`; `main()` CLI with subcommands `scrape` / `index`.

Brochure handling: in `build_doc`, when `source_type == "model"` and `brochure_url` is set, download the PDF via the transport's httpx client and append `extract_pdf_text(...)` to `body` (prefixed with `\n\n[Brochure] `) so its text is searchable in `struct_data` on Standard tier. A separate `brochure` document is also emitted (see Step 3).

- [ ] **Step 1: Write the failing test**

`apps/backend/src/scraper/tests/test_pipeline.py`:
```python
from __future__ import annotations

from pathlib import Path

from scraper.config import ScraperSettings
from scraper.pipeline import build_doc

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = ScraperSettings()


def test_build_doc_model_dispatch_assigns_id() -> None:
    html = (FIXTURES / "model_x50.html").read_text(encoding="utf-8")
    url = "https://www.proton.com/models/all-new-x50"
    # pdf_fetcher returns no bytes → no brochure text appended, no crash.
    doc = build_doc(url, html, SETTINGS, pdf_fetcher=lambda _u: None)
    # doc_id_for() converts dots to underscores; slug_for() keeps them.
    assert doc.doc_id == "www_proton_com_models_all-new-x50"
    assert doc.source_type == "model"
    assert doc.price == "RM 89,800"
    assert doc.gcs_uri.endswith("/www.proton.com_models_all-new-x50.html")


def test_build_doc_generic_dispatch() -> None:
    html = (FIXTURES / "generic_about.html").read_text(encoding="utf-8")
    url = "https://www.proton.com/about-us"
    doc = build_doc(url, html, SETTINGS, pdf_fetcher=lambda _u: None)
    assert doc.source_type == "generic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.pipeline'`.

- [ ] **Step 3: Write the pipeline**

`apps/backend/src/scraper/pipeline.py`:
```python
from __future__ import annotations

from collections.abc import Callable

import structlog

from scraper.classifier import classify
from scraper.config import ScraperSettings
from scraper.extractors.generic import extract_generic
from scraper.extractors.model import extract_model
from scraper.indexer import doc_id_for, slug_for
from scraper.models import ScrapedDoc
from scraper.pdf import extract_pdf_text

_log = structlog.get_logger(__name__)

PdfFetcher = Callable[[str], bytes | None]


def _gcs_uri(settings: ScraperSettings, slug: str) -> str:
    return f"gs://{settings.bucket_name}/{settings.gcs_prefix}/{slug}.html"


def build_doc(
    url: str,
    html: str,
    settings: ScraperSettings,
    pdf_fetcher: PdfFetcher,
) -> ScrapedDoc:
    page_type = classify(url)
    base = extract_model(html, url) if page_type == "model" else extract_generic(html, url)

    slug = slug_for(url)
    body = base.body

    # On Standard tier Vertex can't read PDF content; fold brochure text into body.
    if base.brochure_url:
        data = pdf_fetcher(base.brochure_url)
        if data:
            text = extract_pdf_text(data)
            if text:
                body = f"{body}\n\n[Brochure] {text}"

    from dataclasses import replace

    return replace(base, doc_id=doc_id_for(slug), gcs_uri=_gcs_uri(settings, slug), body=body)


def brochure_doc(parent: ScrapedDoc, settings: ScraperSettings, text: str) -> ScrapedDoc:
    """Emit the brochure as its own citable document."""
    slug = slug_for(parent.brochure_url or f"{parent.link}-brochure")
    return ScrapedDoc(
        doc_id=doc_id_for(slug),
        title=f"{parent.title} — Brochure",
        link=parent.brochure_url or parent.link,
        source_type="brochure",
        language=parent.language,
        body=text,
        gcs_uri=_gcs_uri(settings, slug),
    )
```

`apps/backend/src/scraper/main.py`:
```python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

from scraper.config import ScraperSettings
from scraper.indexer import import_documents, upload_jsonl, write_jsonl
from scraper.pdf import extract_pdf_text
from scraper.pipeline import brochure_doc, build_doc
from scraper.sitemap import fetch_sitemap_urls
from scraper.transport import Transport

_log = structlog.get_logger(__name__)

JSONL_PATH = Path(__file__).parent.parent.parent / "scraped_data" / "proton-kb.jsonl"


def cmd_scrape(settings: ScraperSettings) -> None:
    urls = fetch_sitemap_urls(settings)
    _log.info("scraping", url_count=len(urls))
    transport = Transport(settings)

    def pdf_fetcher(pdf_url: str) -> bytes | None:
        try:
            res = transport._client.get(pdf_url)  # reuse the httpx client
            return res.content if res.status_code == 200 else None
        except Exception:  # noqa: BLE001
            return None

    docs = []
    try:
        for i, url in enumerate(urls, 1):
            html = transport.fetch(url)
            if not html:
                continue
            doc = build_doc(url, html, settings, pdf_fetcher)
            docs.append(doc)
            # Emit a separate brochure document when one exists.
            if doc.source_type == "model" and doc.brochure_url:
                data = pdf_fetcher(doc.brochure_url)
                text = extract_pdf_text(data) if data else ""
                if text:
                    docs.append(brochure_doc(doc, settings, text))
            _log.info("scraped", n=f"{i}/{len(urls)}", url=url)
    finally:
        transport.close()

    write_jsonl(docs, JSONL_PATH)


def cmd_index(settings: ScraperSettings) -> None:
    gcs_uri = upload_jsonl(settings, JSONL_PATH)
    import_documents(settings, gcs_uri)


def main() -> int:
    parser = argparse.ArgumentParser(description="Proton KB scraper")
    parser.add_argument("command", choices=["scrape", "index"])
    args = parser.parse_args()
    settings = ScraperSettings()
    if args.command == "scrape":
        cmd_scrape(settings)
    elif args.command == "index":
        cmd_index(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest src/scraper/tests/test_pipeline.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Full scraper suite + lint + type-check**

Run: `cd apps/backend && .venv/bin/pytest src/scraper -v && .venv/bin/ruff check src/scraper && .venv/bin/mypy src/scraper --strict`
Expected: all pass, no lint/type errors.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/src/scraper/pipeline.py apps/backend/src/scraper/main.py apps/backend/src/scraper/tests/test_pipeline.py
git commit -m "feat(scraper): pipeline dispatch + brochure folding + CLI

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Enrich KbArticle + Vertex adapter pass-through

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/models.py:61-67` (KbArticle)
- Modify: `apps/backend/src/chatbot/features/chat/adapters/vertex_search.py:54-95`
- Test: `apps/backend/src/chatbot/features/chat/test_vertex_search.py` (add cases)

**Interfaces:**
- Consumes: enriched `struct_data` fields written in Phase 1 (`source_type`, `price`, `image_urls`, `brochure_url`).
- Produces: `KbArticle` with optional `source_type: str | None`, `price: str | None`, `image_urls: list[str]`, `brochure_url: str | None` (all defaulted — existing callers unaffected). The adapter populates them.

- [ ] **Step 1: Add the failing test**

Append to `apps/backend/src/chatbot/features/chat/test_vertex_search.py`:
```python
async def test_search_kb_populates_enriched_fields(monkeypatch) -> None:
    from chatbot.features.chat.adapters import vertex_search as vs

    class _StructField:
        def __init__(self, data: dict) -> None:
            self._d = data

        def __bool__(self) -> bool:
            return bool(self._d)

        def __iter__(self):
            return iter(self._d)

        def items(self):
            return self._d.items()

    class _Doc:
        id = "models_x50"
        struct_data = {
            "title": "PROTON All-New X50",
            "link": "https://www.proton.com/models/all-new-x50",
            "source_type": "model",
            "price": "RM 89,800",
            "image_urls": ["https://img/x50.jpg"],
            "brochure_url": "https://img/x50.pdf",
            "body_excerpt": "Compact SUV.",
        }
        derived_struct_data = {}

    class _Result:
        document = _Doc()

    class _Response:
        results = [_Result()]

    class _Client:
        def search(self, request):  # noqa: ARG002
            return _Response()

    monkeypatch.setattr(vs.discoveryengine, "SearchServiceClient", lambda: _Client())

    class _Settings:
        vertex_search_project_id = "p"
        vertex_search_location = "global"
        vertex_search_engine_id = "e"

    adapter = vs.VertexAISearchAdapter(_Settings())
    articles = await adapter.search_kb("x50", limit=1)
    art = articles[0]
    assert art.price == "RM 89,800"
    assert art.source_type == "model"
    assert art.image_urls == ["https://img/x50.jpg"]
    assert art.brochure_url == "https://img/x50.pdf"
```

> Note: match the existing test file's mocking style if it differs; the assertions on `KbArticle` fields are the contract that matters.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_vertex_search.py -v`
Expected: FAIL — `AttributeError: 'KbArticle' object has no attribute 'price'`.

- [ ] **Step 3: Extend KbArticle**

In `apps/backend/src/chatbot/features/chat/models.py`, replace the `KbArticle` dataclass (lines 61-67) with:
```python
@dataclass(frozen=True)
class KbArticle:
    """A single article retrieved from the Knowledge Base."""

    title: str
    content: str
    url: str | None = None
    source_type: str | None = None
    price: str | None = None
    image_urls: list[str] = field(default_factory=list)
    brochure_url: str | None = None
```

- [ ] **Step 4: Populate fields in the adapter**

In `apps/backend/src/chatbot/features/chat/adapters/vertex_search.py`, inside `run_search`, replace the `articles.append(...)` call (around line 93-95) with:
```python
                    raw_images = struct_data.get("image_urls") or []
                    image_urls = [str(u) for u in raw_images] if isinstance(raw_images, list) else []
                    articles.append(
                        KbArticle(
                            title=title,
                            content=content,
                            url=link,
                            source_type=(
                                str(struct_data["source_type"])
                                if struct_data.get("source_type") is not None
                                else None
                            ),
                            price=(
                                str(struct_data["price"])
                                if struct_data.get("price") is not None
                                else None
                            ),
                            image_urls=image_urls,
                            brochure_url=(
                                str(struct_data["brochure_url"])
                                if struct_data.get("brochure_url") is not None
                                else None
                            ),
                        )
                    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_vertex_search.py -v`
Expected: PASS (existing + new test).

- [ ] **Step 6: Full backend suite + lint + type-check**

Run: `cd apps/backend && .venv/bin/pytest src/ && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`
Expected: all pass. (Confirms Zendesk/handoff suites are untouched and green.)

- [ ] **Step 7: Commit**

```bash
git add apps/backend/src/chatbot/features/chat/models.py apps/backend/src/chatbot/features/chat/adapters/vertex_search.py apps/backend/src/chatbot/features/chat/test_vertex_search.py
git commit -m "feat(chat): enrich KbArticle with model fields from struct_data

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Phase 1 live ingest + docs (manual gate)

**Files:**
- Modify: `apps/backend/docs/USAGE.md` (or repo `docs/USAGE.md` — wherever the existing KB pipeline docs live; update the scrape commands)
- Delete: `apps/backend/scripts/scrape_proton.py` (only after parity confirmed)

- [ ] **Step 1: Run the new scraper end to end (requires GCP ADC + Chrome)**

Run:
```bash
cd apps/backend
.venv/bin/python -m scraper.main scrape
.venv/bin/python -m scraper.main index
```
Expected: JSONL written to `apps/backend/scraped_data/proton-kb.jsonl`; GCS upload + import complete.

- [ ] **Step 2: Verify enriched content**

Run: `cd apps/backend && python3 -c "import json; [print(json.loads(l)['struct_data'].get('source_type'), len(json.loads(l)['struct_data'].get('body_excerpt','')), json.loads(l)['struct_data'].get('price')) for l in open('scraped_data/proton-kb.jsonl') if l.strip()][:10]"`
Expected: model rows with `source_type=model`, body length far above 1500, and prices present.

- [ ] **Step 3: Smoke-test the assistant**

Start the backend (`.venv/bin/uvicorn chatbot.main:app --reload`) with `KNOWLEDGE_PROVIDER=vertex_search`, then `POST /chat/turn` with `{"session_id":"sim-1","text":"Tell me about the X50"}`.
Expected: a substantive grounded reply with specs and a citation — **no apology**.

- [ ] **Step 4: Update docs and remove the old script**

Replace the old `scrape_proton.py` usage with the new commands in the USAGE doc, then `git rm apps/backend/scripts/scrape_proton.py`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(scraper): cut over to modular scraper; update docs; remove legacy script

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# PHASE 2 — Product carousel (additive)

## Task 12: Backend ProductCard model + TurnResult.products

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/models.py` (add `ProductCard`; add `products` to `TurnResult`)
- Test: `apps/backend/src/chatbot/features/chat/test_models_product.py` (new)

**Interfaces:**
- Produces: `ProductCard(title: str, image_url: str | None, description: str, price: str | None, url: str | None)`; `TurnResult.products: list[ProductCard]` (default empty).

- [ ] **Step 1: Write the failing test**

`apps/backend/src/chatbot/features/chat/test_models_product.py`:
```python
from __future__ import annotations

from chatbot.features.chat.models import ProductCard, TurnResult


def test_turn_result_defaults_products_empty() -> None:
    tr = TurnResult(reply="hi")
    assert tr.products == []


def test_product_card_fields() -> None:
    card = ProductCard(
        title="X50", image_url="https://img/x50.jpg",
        description="Compact SUV", price="RM 89,800",
        url="https://www.proton.com/models/all-new-x50",
    )
    assert card.title == "X50"
    assert card.price == "RM 89,800"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_models_product.py -v`
Expected: FAIL — `ImportError: cannot import name 'ProductCard'`.

- [ ] **Step 3: Add the model + field**

In `apps/backend/src/chatbot/features/chat/models.py`, add after the `MediaAttachment` dataclass (after line 43):
```python
@dataclass(frozen=True)
class ProductCard:
    """A single product/model card for the A2UI-shaped carousel block."""

    title: str
    description: str
    image_url: str | None = None
    price: str | None = None
    url: str | None = None
```

In the same file, add to `TurnResult` (after the `attachments` field, line 54):
```python
    products: list[ProductCard] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_models_product.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint + type-check + commit**

```bash
cd apps/backend && .venv/bin/ruff check src/ && .venv/bin/mypy src/ --strict
git add apps/backend/src/chatbot/features/chat/models.py apps/backend/src/chatbot/features/chat/test_models_product.py
git commit -m "feat(chat): ProductCard model + TurnResult.products

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: `show_models_tool` + service wiring

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/agents.py:16-98` (add tool + register)
- Modify: `apps/backend/src/chatbot/features/chat/service.py:156-180` (read carousel state)
- Test: `apps/backend/src/chatbot/features/chat/test_show_models.py` (new)

**Interfaces:**
- Consumes: `KnowledgePort.search_kb`, enriched `KbArticle`.
- Produces: `show_models_tool(tool_context, query)` storing `tool_context.state["product_carousel"]` as a list of dicts `{title, description, image_url, price, url}`; `service.handle_turn` maps that state list → `TurnResult.products` (additive, in the SAME post-run block as the handoff read; **handoff logic unchanged**).
- Helper: `_cards_from_state(raw: list[dict]) -> list[ProductCard]` in `service.py`.

- [ ] **Step 1: Write the failing test**

`apps/backend/src/chatbot/features/chat/test_show_models.py`:
```python
from __future__ import annotations

from chatbot.features.chat.service import _cards_from_state


def test_cards_from_state_maps_dicts() -> None:
    raw = [
        {
            "title": "X50",
            "description": "Compact SUV",
            "image_url": "https://img/x50.jpg",
            "price": "RM 89,800",
            "url": "https://www.proton.com/models/all-new-x50",
        }
    ]
    cards = _cards_from_state(raw)
    assert len(cards) == 1
    assert cards[0].title == "X50"
    assert cards[0].image_url == "https://img/x50.jpg"
```

> A direct unit test of the ADK tool closure requires an ADK ToolContext; the
> tool's mapping logic is covered indirectly. The `_cards_from_state` helper is
> the unit-tested seam.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_show_models.py -v`
Expected: FAIL — `ImportError: cannot import name '_cards_from_state'`.

- [ ] **Step 3: Add `show_models_tool` in agents.py**

In `apps/backend/src/chatbot/features/chat/agents.py`, inside `build_ai_agent`, add this tool definition after `search_kb_tool` (after line 40):
```python
    async def show_models_tool(tool_context: ToolContext, query: str) -> str:
        """Fetch Proton model cards to display as a visual carousel.

        Call this when the user asks to see, browse, or compare models, or
        asks which Proton car to buy. Returns an internal confirmation; the
        cards are rendered to the user automatically.

        Args:
            tool_context: Context injected by the ADK runner.
            query: The model/segment the user is interested in.
        """
        articles = await knowledge_port.search_kb(query, limit=6)
        cards = [
            {
                "title": a.title,
                "description": a.content[:200],
                "image_url": a.image_urls[0] if a.image_urls else None,
                "price": a.price,
                "url": a.url,
            }
            for a in articles
            if a.source_type == "model"
        ]
        tool_context.state["product_carousel"] = cards
        return f"[internal] prepared {len(cards)} model cards for the carousel."
```

Then register it in the `Agent(...)` `tools=[...]` list (line 97):
```python
        tools=[search_kb_tool, emit_handoff_tool, show_models_tool],
```

- [ ] **Step 4: Wire service.py (additive — do NOT touch handoff logic)**

In `apps/backend/src/chatbot/features/chat/service.py`, add this module-level helper near the top (after imports, before the class):
```python
def _cards_from_state(raw: list[dict[str, Any]]) -> list["ProductCard"]:
    from chatbot.features.chat.models import ProductCard

    cards: list[ProductCard] = []
    for item in raw:
        cards.append(
            ProductCard(
                title=str(item.get("title", "")),
                description=str(item.get("description", "")),
                image_url=item.get("image_url"),
                price=item.get("price"),
                url=item.get("url"),
            )
        )
    return cards
```

In `handle_turn`, the final `return TurnResult(...)` (lines 175-180) becomes:
```python
        products = _cards_from_state(session_state.get("product_carousel", []) or [])

        return TurnResult(
            reply=reply_text,
            language=session_state.get("language", "unknown"),
            sentiment=session_state.get("sentiment"),
            handoff=handoff_payload,
            products=products,
        )
```

Add `ProductCard` to the existing `models` import block (lines 15-20):
```python
from chatbot.features.chat.models import (
    HandoffOpenPayload,
    HandoffPayload,
    Message,
    ProductCard,
    TurnResult,
)
```
(and remove the local `from ... import ProductCard` inside `_cards_from_state`, importing at module scope instead.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_show_models.py src/chatbot/features/chat/ -v`
Expected: PASS, including all existing handoff/service tests (proves Zendesk paths intact).

- [ ] **Step 6: Lint + type-check + commit**

```bash
cd apps/backend && .venv/bin/ruff check src/ && .venv/bin/mypy src/ --strict
git add apps/backend/src/chatbot/features/chat/agents.py apps/backend/src/chatbot/features/chat/service.py apps/backend/src/chatbot/features/chat/test_show_models.py
git commit -m "feat(chat): show_models_tool + carousel state wiring (additive)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Router response + prompt update

**Files:**
- Modify: `apps/backend/src/chatbot/features/chat/router.py:33-38` (ChatTurnResponse) and `:227-237` (chat_turn)
- Modify: `apps/backend/src/chatbot/features/chat/prompts.py:3-40` (add carousel instruction)
- Test: `apps/backend/src/chatbot/features/chat/test_router_products.py` (new)

**Interfaces:**
- Consumes: `TurnResult.products`.
- Produces: `ChatTurnResponse.products: list[dict[str, Any]]` serialized as `{title, description, image_url, price, url}`.

- [ ] **Step 1: Write the failing test**

`apps/backend/src/chatbot/features/chat/test_router_products.py`:
```python
from __future__ import annotations

from chatbot.features.chat.models import ProductCard, TurnResult
from chatbot.features.chat.router import _products_list


def test_products_list_serializes_cards() -> None:
    tr = TurnResult(
        reply="Here are some models:",
        products=[
            ProductCard(
                title="X50", description="Compact SUV",
                image_url="https://img/x50.jpg", price="RM 89,800",
                url="https://www.proton.com/models/all-new-x50",
            )
        ],
    )
    out = _products_list(tr)
    assert out == [
        {
            "title": "X50",
            "description": "Compact SUV",
            "image_url": "https://img/x50.jpg",
            "price": "RM 89,800",
            "url": "https://www.proton.com/models/all-new-x50",
        }
    ]


def test_products_list_empty_by_default() -> None:
    assert _products_list(TurnResult(reply="hi")) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_router_products.py -v`
Expected: FAIL — `ImportError: cannot import name '_products_list'`.

- [ ] **Step 3: Add serializer + response field**

In `apps/backend/src/chatbot/features/chat/router.py`, extend `ChatTurnResponse` (lines 33-38):
```python
class ChatTurnResponse(BaseModel):
    reply: str | None
    language: str | None = None
    sentiment: str | None = None
    handoff: dict[str, Any] | None = None
    forwarded_to_agent: bool = False
    products: list[dict[str, Any]] = []
```

Add a helper next to `_handoff_dict` (after line 50):
```python
def _products_list(turn_result: Any) -> list[dict[str, Any]]:
    return [
        {
            "title": p.title,
            "description": p.description,
            "image_url": p.image_url,
            "price": p.price,
            "url": p.url,
        }
        for p in turn_result.products
    ]
```

In `chat_turn`, add `products=_products_list(result)` to the `ChatTurnResponse(...)` (lines 231-237):
```python
        return ChatTurnResponse(
            reply=result.reply,
            language=result.language,
            sentiment=result.sentiment,
            handoff=_handoff_dict(result),
            forwarded_to_agent=result.forwarded_to_agent,
            products=_products_list(result),
        )
```

- [ ] **Step 4: Update the prompt (additive — leave all escalation rules intact)**

In `apps/backend/src/chatbot/features/chat/prompts.py`, add a new bullet to Step 2 of `AGENT_INSTRUCTION` (after the citation bullet, before the escalation section). Insert this string segment after line 21 (`"the Source URL is itself a useful answer.\n\n"`):
```python
    "## Showing models visually\n"
    "When the customer asks to see, browse, or compare Proton models, or asks "
    "which car/SUV/sedan to buy, call `show_models_tool` with a concise query "
    "in ADDITION to answering. Write a brief one-line intro (e.g. \"Here are "
    "some models you might like:\") — the model cards are shown to the customer "
    "automatically, so do not describe each car in long prose when the carousel "
    "is displayed.\n\n"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest src/chatbot/features/chat/test_router_products.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Full backend suite + lint + type-check**

Run: `cd apps/backend && .venv/bin/pytest src/ && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/src/chatbot/features/chat/router.py apps/backend/src/chatbot/features/chat/prompts.py apps/backend/src/chatbot/features/chat/test_router_products.py
git commit -m "feat(chat): expose products in ChatTurnResponse + carousel prompt

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Frontend types

**Files:**
- Modify: `apps/frontend/src/features/chat/types/index.ts`

**Interfaces:**
- Produces: `ProductCard` interface; `ChatTurnResponse.products`; `ChatMessage.products?`.

- [ ] **Step 1: Edit types**

In `apps/frontend/src/features/chat/types/index.ts`, add the `ProductCard` interface and extend `ChatTurnResponse` + `ChatMessage`:
```typescript
export interface ProductCard {
  title: string;
  description: string;
  image_url: string | null;
  price: string | null;
  url: string | null;
}

export interface ChatTurnResponse {
  reply: string | null;
  language: string | null;
  sentiment: string | null;
  handoff: HandoffPayload | null;
  forwarded_to_agent: boolean;
  products: ProductCard[];
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'agent';
  text: string;
  meta?: string;
  products?: ProductCard[];
}
```
(Replace the existing `ChatTurnResponse` and `ChatMessage` interfaces; leave `HandoffPayload` and `AgentMessageEvent` unchanged.)

- [ ] **Step 2: Type-check**

Run: `cd apps/frontend && npm run type-check`
Expected: passes (no usages broken yet).

- [ ] **Step 3: Commit**

```bash
git add apps/frontend/src/features/chat/types/index.ts
git commit -m "feat(chat-fe): ProductCard type + products on response/message

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: ProductCarousel.vue component

**Files:**
- Create: `apps/frontend/src/features/chat/components/ProductCarousel.vue`

**Interfaces:**
- Consumes: `ProductCard[]` via prop `items`.
- Produces: a horizontally scrolling card row using the design tokens.

- [ ] **Step 1: Create the component**

`apps/frontend/src/features/chat/components/ProductCarousel.vue`:
```vue
<script setup lang="ts">
import type { ProductCard } from '@/features/chat/types';

defineProps<{ items: ProductCard[] }>();
</script>

<template>
  <div class="carousel" role="list">
    <article v-for="(item, i) in items" :key="i" class="card" role="listitem">
      <img
        v-if="item.image_url"
        :src="item.image_url"
        :alt="item.title"
        class="card-img"
        loading="lazy"
      />
      <div class="card-body">
        <h4 class="card-title">{{ item.title }}</h4>
        <p v-if="item.price" class="card-price">{{ item.price }}</p>
        <p class="card-desc">{{ item.description }}</p>
        <a
          v-if="item.url"
          :href="item.url"
          target="_blank"
          rel="noopener noreferrer"
          class="card-link"
        >Learn more</a>
      </div>
    </article>
  </div>
</template>

<style scoped>
.carousel {
  display: flex;
  gap: var(--space-md);
  overflow-x: auto;
  padding: var(--space-sm) 0;
  scroll-snap-type: x mandatory;
}
.card {
  flex: 0 0 220px;
  scroll-snap-align: start;
  background: var(--surface-elevated, var(--surface));
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.card-img {
  width: 100%;
  height: 130px;
  object-fit: cover;
  background: var(--bg);
}
.card-body {
  padding: var(--space-sm);
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
}
.card-title {
  margin: 0;
  font-size: 0.95rem;
  color: var(--text);
}
.card-price {
  margin: 0;
  color: var(--accent);
  font-weight: 600;
  font-size: 0.85rem;
}
.card-desc {
  margin: 0;
  font-size: 0.8rem;
  color: var(--text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-link {
  margin-top: auto;
  font-size: 0.8rem;
  color: var(--assistant);
  text-decoration: underline;
}
.card-link:hover { text-decoration: none; }
</style>
```

- [ ] **Step 2: Type-check**

Run: `cd apps/frontend && npm run type-check`
Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add apps/frontend/src/features/chat/components/ProductCarousel.vue
git commit -m "feat(chat-fe): ProductCarousel component

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Wire carousel into store + ChatLog

**Files:**
- Modify: `apps/frontend/src/features/chat/store/chat.store.ts:103-111` (attach products)
- Modify: `apps/frontend/src/features/chat/components/ChatLog.vue` (render carousel)

**Interfaces:**
- Consumes: `ChatTurnResponse.products`, `ProductCarousel.vue`.

- [ ] **Step 1: Attach products in the store**

In `apps/frontend/src/features/chat/store/chat.store.ts`, update the assistant push (lines 107-111) to include products:
```typescript
        messages.value.push({
          role: 'assistant',
          text: result.reply,
          meta: metaBits.length ? metaBits.join(' · ') : undefined,
          products: result.products?.length ? result.products : undefined,
        });
```

- [ ] **Step 2: Render the carousel in ChatLog**

In `apps/frontend/src/features/chat/components/ChatLog.vue`:

Add the import in `<script setup>` (after line 3):
```typescript
import ProductCarousel from '@/features/chat/components/ProductCarousel.vue';
```

In the template, add the carousel after the assistant text `<p>` (after line 43, before the `<footer>`):
```vue
      <ProductCarousel
        v-if="msg.products && msg.products.length"
        :items="msg.products"
      />
```

- [ ] **Step 3: Type-check + build**

Run: `cd apps/frontend && npm run type-check && npm run build`
Expected: both pass.

- [ ] **Step 4: Manual verification**

Start backend (`KNOWLEDGE_PROVIDER=vertex_search`) + frontend (`npm run dev`). Ask "show me your SUVs".
Expected: a short intro line followed by an inline carousel of model cards (image + price + Learn more). Ask a non-product question → no carousel, plain grounded answer.

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/src/features/chat/store/chat.store.ts apps/frontend/src/features/chat/components/ChatLog.vue
git commit -m "feat(chat-fe): render product carousel under assistant replies

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Backend: `cd apps/backend && .venv/bin/pytest src/ && .venv/bin/ruff check . && .venv/bin/mypy src/ --strict` — all green.
- [ ] Frontend: `cd apps/frontend && npm run type-check && npm run build` — both pass.
- [ ] Confirm Zendesk untouched: `git diff main --stat -- apps/backend/src/chatbot/features/chat/adapters/zendesk.py` shows no changes; handoff tests still pass.
- [ ] Manual: "Tell me about the X50" → grounded specs, no apology. "Show me your SUVs" → carousel.
