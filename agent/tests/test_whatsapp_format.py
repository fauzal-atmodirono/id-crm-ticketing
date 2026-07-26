"""Ported from the standalone proton-conversational-ai WhatsApp send path
(`router._md_to_whatsapp` + `twilio_channel._chunk_whatsapp_body`)."""

from app.services import whatsapp_format as wf


def test_md_to_whatsapp_converts_bold_headings_bullets_links():
    out = wf.md_to_whatsapp(
        "# Proton S70\n**Performa** hebat\n- Cepat\n- [Specs](https://proton.com/s70)"
    )
    assert "*Proton S70*" in out  # heading -> *bold*
    assert "*Performa*" in out  # ** -> * (WhatsApp bold)
    assert "• Cepat" in out  # "- " -> bullet
    assert "Specs (https://proton.com/s70)" in out  # link flattened
    assert "**" not in out
    assert "](http" not in out


def test_md_to_whatsapp_plain_text_unchanged():
    assert wf.md_to_whatsapp("hello there") == "hello there"
    assert wf.md_to_whatsapp("") == ""


def test_chunk_whatsapp_short_single_and_empty():
    assert wf.chunk_whatsapp("hi") == ["hi"]
    assert wf.chunk_whatsapp("") == []
    assert wf.chunk_whatsapp("   ") == []


def test_chunk_whatsapp_splits_over_limit_without_losing_content():
    text = ("word " * 500).strip()  # ~2500 chars
    chunks = wf.chunk_whatsapp(text, limit=1600)
    assert len(chunks) >= 2
    assert all(len(c) <= 1600 for c in chunks)
    assert "".join(c.replace(" ", "") for c in chunks) == text.replace(" ", "")
