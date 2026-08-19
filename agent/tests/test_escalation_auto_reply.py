"""An out-of-office is not an acknowledgement.

Why this has its own file: an away message arrives from the same allowlisted
dealer address as a real answer, so before this check it stamped
`escalation_replied_at` -- which halts the escalation ladder, satisfies the
acknowledgement SLA and starts the customer-update clock. "I am on leave
until Monday" would have satisfied the entire escalation policy.
"""

from __future__ import annotations

import httpx
import respx

from app.config import get_settings
from app.services import escalation_replies
from app.services.escalation_replies import is_auto_reply

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


def _msg(subject: str = "Re: [CASE-42] charger", **email: object) -> dict:
    return {
        "message_type": "incoming",
        "content": "I am away until Monday.",
        "sender": {"email": "cre@kl.my"},
        "conversation": {"id": 77},
        "inbox": {"id": 4},
        "content_attributes": {
            "email": {"to": ["support+case42@test"], "subject": subject, **email}
        },
    }


# --- detection --------------------------------------------------------------


def test_the_common_subject_prefixes_are_caught() -> None:
    for subject in (
        "Automatic reply: charger",
        "Auto-Reply: charger",
        "Out of Office: charger",
        "On leave until Monday",
        "Balasan automatik: caj",
    ):
        assert is_auto_reply(_msg(subject)), subject


def test_a_stacked_reply_prefix_is_stripped_first() -> None:
    assert is_auto_reply(_msg("Re: Automatic reply: charger"))
    assert is_auto_reply(_msg("Fwd: Out of office"))


def test_the_rfc_header_is_honoured_when_chatwoot_provides_it() -> None:
    assert is_auto_reply(_msg("charger", auto_submitted="auto-replied"))
    assert is_auto_reply(_msg("charger", headers={"X-Autoreply": "yes"}))


def test_auto_submitted_no_is_a_real_person() -> None:
    """RFC 3834 says `no` is what ordinary mail carries."""
    assert not is_auto_reply(_msg("charger", auto_submitted="no"))


def test_a_human_writing_about_autoresponders_is_not_one() -> None:
    """Prefix match, not substring: this is a dealer answering the case."""
    assert not is_auto_reply(_msg("Re: our automatic reply system failed, here is the fix"))


def test_an_ordinary_reply_is_not_an_auto_reply() -> None:
    assert not is_auto_reply(_msg())
    assert not is_auto_reply({})


# --- behaviour --------------------------------------------------------------


def _enable(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "escalation_reply_linking_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", PROTON)
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")


@respx.mock
async def test_an_auto_reply_is_noted_but_never_stamped(monkeypatch):
    from app.clients.deps import get_chatwoot_client, get_proton_config_client

    _enable(monkeypatch)
    get_proton_config_client.cache_clear()
    get_chatwoot_client.cache_clear()

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/4").mock(
        return_value=httpx.Response(200, json={"id": 4, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(
            200, json={"id": 42, "custom_attributes": {}, "meta": {"sender": {"email": "c@x.my"}}}
        )
    )
    respx.get(f"{PROTON}/escalation/contacts").mock(
        return_value=httpx.Response(
            200, json={"contacts": [{"email": "cre@kl.my", "name": "KL CRE", "kind": "dealer"}]}
        )
    )
    note = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={})
    )
    stamp = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={}))
    labels = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/77/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/77/toggle_status").mock(
        return_value=httpx.Response(200, json={})
    )

    await escalation_replies.maybe_link_escalation_reply(_msg("Automatic reply: charger"))

    assert note.called
    assert "not counted as a response" in note.calls[0].request.content.decode()
    # The three things that would have satisfied the escalation policy:
    assert not stamp.called      # escalation_replied_at
    assert not labels.called     # escalation_replied -> the ladder keeps climbing
    get_proton_config_client.cache_clear()
