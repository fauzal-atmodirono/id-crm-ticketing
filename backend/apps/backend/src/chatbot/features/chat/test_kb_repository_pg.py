# test_kb_repository_pg.py
import os

import pytest

from chatbot.features.chat.kb_db import build_engine, build_session_maker, init_kb_db
from chatbot.features.chat.kb_repository import PgKbRepository

_URL = os.environ.get("KB_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(_URL is None, reason="KB_TEST_DATABASE_URL not set")


async def test_pg_ingest_and_search_roundtrip() -> None:
    engine = build_engine(_URL)
    await init_kb_db(engine)
    repo = PgKbRepository(build_session_maker(engine))

    doc_id = await repo.create_document(
        title="Warranty", source_type="text",
        original_filename=None, mime_type=None, char_count=0,
    )
    near = [1.0] + [0.0] * 767
    far = [0.0] * 767 + [1.0]
    await repo.add_chunks(doc_id, [(0, "warranty five years", near, 19),
                                   (1, "unrelated", far, 9)])
    await repo.set_status(doc_id, "indexed")

    hits = await repo.search_chunks(near, limit=2)
    assert hits[0].content == "warranty five years"
    assert hits[0].score > hits[1].score

    assert await repo.delete_document(doc_id) is True
    await engine.dispose()
