from chatbot.features.chat.kb_ingest import chunk_text


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("", 100, 10) == []
    assert chunk_text("   ", 100, 10) == []


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("hello world", 100, 10) == ["hello world"]


def test_long_text_splits_on_word_boundaries_with_overlap() -> None:
    text = " ".join(f"w{i}" for i in range(20))  # each token ~3 chars
    chunks = chunk_text(text, max_chars=20, overlap_chars=6)
    assert len(chunks) > 1
    # never splits mid-word
    for c in chunks:
        assert "  " not in c
        assert len(c) <= 20
    # consecutive chunks share overlap words
    assert chunks[0].split()[-1] in chunks[1].split()
