"""P2 task 4 — carry the customer's evidence into the escalation mail.

A complaint escalation whose photo of the damaged part stays behind in Chatwoot
makes the PIC open the CRM to see what they were sent. Attaching it is the
difference between an actionable email and a notification.

Two properties matter more than the feature:

* A download failure produces a *note in the body*, never a failed escalation.
  Losing the photo is a nuisance; losing the escalation is the bug this whole
  package exists to eliminate.
* With the flag off there is no HTTP call at all -- not a call whose result is
  discarded.
"""

from __future__ import annotations

from typing import Any

from chatbot.features.chat.escalation_attachments import DEFAULT_ALLOWED, collect


class _Fetcher:
    """Stands in for the Chatwoot messages API plus the file download."""

    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        blobs: dict[str, bytes] | None = None,
        fails: set[str] | None = None,
        list_raises: bool = False,
    ) -> None:
        self._messages = messages or []
        self._blobs = blobs or {}
        self._fails = fails or set()
        self._list_raises = list_raises
        self.list_calls = 0
        self.download_calls: list[str] = []

    async def list_messages(self, conv_id: str) -> list[dict[str, Any]]:
        del conv_id
        self.list_calls += 1
        if self._list_raises:
            raise RuntimeError("chatwoot down")
        return self._messages

    async def download(self, url: str) -> bytes:
        self.download_calls.append(url)
        if url in self._fails:
            raise RuntimeError("404")
        return self._blobs.get(url, b"")


def _msg(created_at: int, *attachments: dict[str, Any]) -> dict[str, Any]:
    return {"created_at": created_at, "attachments": list(attachments)}


def _att(url: str, name: str, mimetype: str, size: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"data_url": url, "file_name": name, "file_type": mimetype}
    if size is not None:
        out["file_size"] = size
    return out


async def test_attachments_within_budget_are_all_returned():
    fetcher = _Fetcher(
        messages=[_msg(1, _att("u1", "a.jpg", "image/jpeg")), _msg(2, _att("u2", "b.png", "image/png"))],
        blobs={"u1": b"x" * 10, "u2": b"y" * 10},
    )
    files, skipped = await collect(fetcher, "42", budget_bytes=1000, allowed=DEFAULT_ALLOWED)

    assert [f[0] for f in files] == ["b.png", "a.jpg"]  # newest first
    assert skipped == []


async def test_newest_attachments_are_preferred_when_the_budget_is_exceeded():
    """The most recent evidence is the most relevant to what was just
    escalated."""
    fetcher = _Fetcher(
        messages=[
            _msg(1, _att("old", "old.jpg", "image/jpeg")),
            _msg(9, _att("new", "new.jpg", "image/jpeg")),
        ],
        blobs={"old": b"o" * 100, "new": b"n" * 100},
    )
    files, skipped = await collect(fetcher, "42", budget_bytes=150, allowed=DEFAULT_ALLOWED)

    assert [f[0] for f in files] == ["new.jpg"]
    assert any("old.jpg" in note for note in skipped)


async def test_skipped_attachments_are_described_not_silently_dropped():
    fetcher = _Fetcher(
        messages=[_msg(1, _att("big", "huge.jpg", "image/jpeg"))],
        blobs={"big": b"b" * 500},
    )
    files, skipped = await collect(fetcher, "42", budget_bytes=100, allowed=DEFAULT_ALLOWED)

    assert files == []
    assert len(skipped) == 1
    assert "huge.jpg" in skipped[0]


async def test_a_download_failure_yields_a_skip_note_and_does_not_raise():
    fetcher = _Fetcher(
        messages=[_msg(1, _att("gone", "missing.jpg", "image/jpeg"))],
        fails={"gone"},
    )
    files, skipped = await collect(fetcher, "42", budget_bytes=1000, allowed=DEFAULT_ALLOWED)

    assert files == []
    assert any("missing.jpg" in note for note in skipped)


async def test_a_disallowed_mimetype_is_skipped_with_a_reason():
    fetcher = _Fetcher(
        messages=[_msg(1, _att("exe", "payload.exe", "application/x-msdownload"))],
        blobs={"exe": b"MZ"},
    )
    files, skipped = await collect(fetcher, "42", budget_bytes=1000, allowed=DEFAULT_ALLOWED)

    assert files == []
    assert any("payload.exe" in note for note in skipped)
    assert not fetcher.download_calls, "a disallowed type must not be downloaded"


async def test_a_conversation_with_no_attachments_returns_empty_lists():
    fetcher = _Fetcher(messages=[_msg(1)])
    assert await collect(fetcher, "42", budget_bytes=1000, allowed=DEFAULT_ALLOWED) == ([], [])


async def test_a_listing_failure_degrades_to_nothing_rather_than_raising():
    fetcher = _Fetcher(list_raises=True)
    files, skipped = await collect(fetcher, "42", budget_bytes=1000, allowed=DEFAULT_ALLOWED)
    assert files == []
    assert skipped and "could not be read" in skipped[0]


async def test_a_zero_budget_collects_nothing_and_downloads_nothing():
    """The flag-off path: no HTTP call at all, not a discarded result."""
    fetcher = _Fetcher(
        messages=[_msg(1, _att("u1", "a.jpg", "image/jpeg"))], blobs={"u1": b"x"}
    )
    files, skipped = await collect(fetcher, "42", budget_bytes=0, allowed=DEFAULT_ALLOWED)

    assert (files, skipped) == ([], [])
    assert fetcher.list_calls == 0
    assert fetcher.download_calls == []


async def test_the_declared_size_is_used_to_skip_before_downloading():
    """An oversized file should not be pulled over the wire just to discard
    it -- Chatwoot already tells us how big it is."""
    fetcher = _Fetcher(
        messages=[_msg(1, _att("big", "huge.jpg", "image/jpeg", size=10_000))],
    )
    files, skipped = await collect(fetcher, "42", budget_bytes=100, allowed=DEFAULT_ALLOWED)

    assert files == []
    assert skipped
    assert fetcher.download_calls == []


# --- wired into the notifier ----------------------------------------------


from chatbot.features.chat.escalation_notifier import EscalationNotifier  # noqa: E402
from chatbot.features.chat.pic_registry import PicEntry  # noqa: E402


class _Sender:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, to, cc, subject, body, attachments, *, reply_to=None):
        self.calls.append({"to": to, "body": body, "attachments": attachments})

    def by_to(self, address):
        return next((c for c in self.calls if c["to"] and c["to"][0] == address), None)


class _Settings:
    escalation_email_enabled = True
    email_escalation_ack_enabled = True
    email_escalation_ack_template = "ack body"
    escalation_ack_chat_template = ""
    escalation_cc_pic = True
    escalation_cc_dealer = False
    escalation_reply_to_template = ""
    dealer_email_map_json = ""
    escalation_attachment_budget_bytes = 1000


class _DealerStore:
    async def get(self, dealer):
        from chatbot.features.chat.pic_store import DealerRecord

        return DealerRecord(dealer=dealer, emails=["dealer@test"])


class _Registry:
    async def lookup(self, dept):
        del dept
        return PicEntry(pic_name="Aduy", pic_email="pic@test", pic_whatsapp="")


async def _noop_cw(conv_id, attrs):
    return None


async def _notify(sender, fetcher, settings):
    notifier = EscalationNotifier(
        settings,
        _Registry(),
        sender,
        None,
        _noop_cw,
        dealer_store=_DealerStore(),
        attachment_fetcher=fetcher,
    )
    await notifier.notify_escalation(
        conv_id="42",
        title="t",
        body="transcript",
        department="sales",
        dealer="komang_motor",
        customer_email="customer@test",
        ack_transport="email",
    )


async def test_the_customer_ack_never_receives_attachments():
    sender = _Sender()
    fetcher = _Fetcher(
        messages=[_msg(1, _att("u1", "damage.jpg", "image/jpeg"))],
        blobs={"u1": b"x" * 10},
    )
    await _notify(sender, fetcher, _Settings())

    assert sender.by_to("pic@test")["attachments"], "the PIC should get the photo"
    assert sender.by_to("customer@test")["attachments"] == []


async def test_both_internal_legs_receive_the_same_attachments():
    sender = _Sender()
    fetcher = _Fetcher(
        messages=[_msg(1, _att("u1", "damage.jpg", "image/jpeg"))],
        blobs={"u1": b"x" * 10},
    )
    await _notify(sender, fetcher, _Settings())

    assert sender.by_to("pic@test")["attachments"] == sender.by_to("dealer@test")["attachments"]


async def test_the_files_are_fetched_once_for_both_legs():
    sender = _Sender()
    fetcher = _Fetcher(
        messages=[_msg(1, _att("u1", "damage.jpg", "image/jpeg"))],
        blobs={"u1": b"x" * 10},
    )
    await _notify(sender, fetcher, _Settings())

    assert fetcher.download_calls == ["u1"]


async def test_the_skip_notes_are_appended_to_the_pic_and_dealer_email_body():
    sender = _Sender()
    settings = _Settings()
    settings.escalation_attachment_budget_bytes = 5
    fetcher = _Fetcher(
        messages=[_msg(1, _att("u1", "huge.jpg", "image/jpeg", size=999))],
    )
    await _notify(sender, fetcher, settings)

    assert "huge.jpg" in sender.by_to("pic@test")["body"]
    assert "huge.jpg" in sender.by_to("dealer@test")["body"]
    assert "huge.jpg" not in sender.by_to("customer@test")["body"]


async def test_the_flag_off_makes_no_http_call_at_all():
    sender = _Sender()
    settings = _Settings()
    settings.escalation_attachment_budget_bytes = 0
    fetcher = _Fetcher(
        messages=[_msg(1, _att("u1", "damage.jpg", "image/jpeg"))],
        blobs={"u1": b"x"},
    )
    await _notify(sender, fetcher, settings)

    assert fetcher.list_calls == 0
    assert sender.by_to("pic@test")["attachments"] == []
