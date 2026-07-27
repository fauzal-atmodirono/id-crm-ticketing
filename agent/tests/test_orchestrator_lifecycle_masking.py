"""Regression: a lifecycle system message (disclaimer / idle-warn / etc.) must
not mask the customer's turn for the brain-swap agent.

Root cause of the live bug: `lifecycle.on_conversation_created` posts the AI
disclaimer as an OUTGOING, non-private message right after the customer's first
message. `_latest_incoming_text` walked messages backward and broke at the first
outgoing message, treating the disclaimer as "the bot already replied" — so the
customer's "halo" was masked and `/chat/turn` was never called.

Fix: lifecycle stamps its customer-facing messages with
`content_attributes={"proton_lifecycle": True}`, and `_latest_incoming_text`
skips those (they are system notices, not the bot's conversational reply).
"""

from app.services import orchestrator


def _in(content):
    return {"message_type": 0, "private": False, "content": content}


def _out(content, *, lifecycle=False):
    msg = {"message_type": 1, "private": False, "content": content}
    if lifecycle:
        msg["content_attributes"] = {"proton_lifecycle": True}
    return msg


def test_disclaimer_does_not_mask_first_customer_message():
    # halo -> disclaimer(lifecycle). The disclaimer must be skipped so the
    # customer's first message still reaches the backend.
    messages = [_in("halo"), _out("DISCLAIMER: ...", lifecycle=True)]
    assert orchestrator._latest_incoming_text(messages) == "halo"


def test_real_bot_reply_still_bounds_the_turn():
    # An unmarked outgoing reply IS the bot speaking: only text after it counts.
    messages = [_in("halo"), _out("Hi! How can I help?"), _in("spec S70?")]
    assert orchestrator._latest_incoming_text(messages) == "spec S70?"


def test_lifecycle_message_between_customer_turns_is_transparent():
    # halo -> disclaimer(lifecycle) -> spec. Both customer turns are collected
    # because the lifecycle message is not a real reply boundary.
    messages = [
        _in("halo"),
        _out("DISCLAIMER: ...", lifecycle=True),
        _in("spec S70?"),
    ]
    assert orchestrator._latest_incoming_text(messages) == "halo\nspec S70?"


def test_plain_trailing_incoming_unchanged():
    messages = [_out("earlier reply"), _in("new question")]
    assert orchestrator._latest_incoming_text(messages) == "new question"


def test_native_greeting_is_skipped_by_content_match():
    # halo -> native greeting (outgoing, no marker). Passing the greeting text
    # lets the orchestrator skip it so "halo" still reaches the backend.
    greeting = "DISCLAIMER: native greeting text"
    messages = [_in("halo"), _out(greeting)]
    assert orchestrator._latest_incoming_text(messages, greeting) == "halo"


def test_non_greeting_outgoing_still_bounds_turn():
    # A real bot reply (not the greeting) still bounds the turn even when a
    # greeting_text is supplied.
    messages = [_in("halo"), _out("Hi! How can I help?"), _in("spec?")]
    assert orchestrator._latest_incoming_text(messages, "some greeting") == "spec?"


def test_empty_greeting_text_preserves_behavior():
    # No greeting supplied -> unchanged: the outgoing message bounds the turn.
    messages = [_in("halo"), _out("Hi! How can I help?")]
    assert orchestrator._latest_incoming_text(messages, "") == ""
