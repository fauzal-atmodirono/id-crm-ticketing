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
