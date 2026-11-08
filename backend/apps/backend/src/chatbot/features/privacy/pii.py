"""Personal-data patterns and masking, shared by the audit trail and the
PII masking gate (Wave 2 Task 29, FIX-49; RFP 2026_028 2.2.5; PDPA 2010).

Two masking styles live here, deliberately kept apart:

* **Audit redaction** (``mask_text`` / ``redact`` / ``mask_recipient``) -- the
  Task 19 format written INTO the append-only trail: e-mail keeps the first
  character and the domain (``a***@kl.my``), phone-like numbers keep the last
  three digits (``********789``), secret-looking keys are replaced. It is
  applied unconditionally at write time, so the trail never holds the raw
  value. Moved here unchanged from ``features/chat/audit_events.py`` (which
  re-exports it); its tests pin the exact output, so do not "improve" it.

* **Display masking** (``mask_pii_text`` / ``mask_pii``) -- applied to a
  backend RESPONSE for a caller without ``pii.view`` when
  ``PII_MASKING_ENABLED`` is on (see ``access.py``). Broader rule list (MyKad,
  cards, VIN, passport as well as phone and e-mail) and a display format that
  keeps the last 4: ``+60•••••6789``, ``s•••@gmail.com``, ``******-**-5678``.
  Returns a copy; the input is never changed, so a shared cache can serve
  masked and unmasked callers from the same object.

Default display rules, for Proton compliance to confirm (``DEFAULT_RULES``):

=============  =====================================================  ==========================
rule           matches (free text)                                    shown as
=============  =====================================================  ==========================
email          ``local@domain``                                       ``a•••@domain``
mykad_ic       ``YYMMDD-PB-####`` or 12 digits; valid month/day,      ``******-**-1234``
               place-of-birth code not ``00``
card_luhn      13-19 digits (spaces/dashes allowed), first digit      ``••••••••••••1234``
               2-6, Luhn-valid
phone          ``+<cc>`` or ``0`` prefix, 9-15 digits, not date-shaped  ``+60•••••1234``/``01••••1234``
vin_chassis    17 chars ``[A-HJ-NPR-Z0-9]`` with >=2 letters, >=2 digits  ``•••••••••••••1234``
passport_my    Malaysian ``A``/``H``/``K`` + 8 digits                  ``•••••5678``
=============  =====================================================  ==========================

Structured fields are also masked by KEY whatever the value's shape (stored
numbers are not always written with a ``+`` or ``0``): phone / mobile /
whatsapp / msisdn / tel, email, IC / NRIC / MyKad, passport, VIN / chassis,
bank account and card. Not masked: customer names and vehicle plates (an
agent needs both to work a case -- not on the RFP's identifier list); bank
account numbers in FREE text (no structure to tell them from any other digit
run -- key-based only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MASK = "•"

# ---------------------------------------------------------------------------
# Audit redaction (Task 19) -- moved verbatim from audit_events.py.
# ---------------------------------------------------------------------------

_SECRET_KEY = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|credential|authorization)", re.I
)
_EMAIL = re.compile(
    r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,}|[A-Za-z0-9-]+)"
)
# Phone-like structure only: a leading "+" (country code) or "0" (national
# trunk prefix), then digits with optional space/dash/dot/paren separators, 9-15
# digits in all. Free text is full of other digit runs -- ISO timestamps, case
# and ticket numbers, ids -- which must survive untouched, so a bare run of
# digits is NOT treated as a phone. The look-arounds stop a match starting
# inside a longer token ("2026-11-09", "ab0123456789").
_PHONE = re.compile(r"(?<![\w:+-])(?:\+\d|0)[\d\s().-]{7,19}\d(?![\w:-])")
_DATE_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}")
# Keys whose VALUE is a phone number whatever its shape (stored numbers are not
# always written with a leading + or 0).
_PHONE_KEY = re.compile(r"(whatsapp|phone|mobile|msisdn|tel)", re.I)
_MIN_PHONE_DIGITS, _MAX_PHONE_DIGITS = 9, 15
_REDACTED = "[redacted]"


def _mask_digits(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) <= 3:  # noqa: PLR2004 -- nothing worth keeping
        return "*" * len(digits)
    return f"{'*' * (len(digits) - 3)}{digits[-3:]}"


def _mask_phone(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if not _MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS or _DATE_SHAPE.search(raw):
        return raw
    return _mask_digits(raw)


def mask_recipient(value: str) -> str:
    """Recipient as stored in the trail.

    E-mail addresses are kept in full: RFP 3.2.6 asks the trail to name the
    recipient, and an address is how a delivery is checked. A phone-shaped
    recipient (phone-task contacts) is a different matter -- nothing about
    auditing a call needs the whole number -- so it is masked to the last 3
    digits. Decision recorded here so it is not rediscovered as a bug.
    """
    if "@" in value:
        return value
    return _mask_digits(value) if re.search(r"\d{6,}", value) else value


def mask_text(value: str) -> str:
    """Mask e-mail addresses and phone-like numbers inside a free-text string."""
    value = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value)
    return _PHONE.sub(_mask_phone, value)


def redact(value: Any) -> Any:
    """Deep-copy ``value`` with secrets replaced and personal contact data masked."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _SECRET_KEY.search(str(k)) and v:
                out[str(k)] = _REDACTED
            elif _PHONE_KEY.search(str(k)) and isinstance(v, str):
                out[str(k)] = _mask_digits(v)
            else:
                out[str(k)] = redact(v)
        return out
    if isinstance(value, (list, tuple, set)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    return value


# ---------------------------------------------------------------------------
# Display masking (Task 29).
# ---------------------------------------------------------------------------

_KEEP = 4
_MIN_IC_TAIL = 4


@dataclass(frozen=True)
class PiiRule:
    """One free-text rule: what it finds and how a hit is shown."""

    name: str
    description: str
    pattern: re.Pattern[str]


def _tail(raw: str, keep: int = _KEEP) -> str:
    """Bullets for all but the last ``keep`` alphanumerics (separators dropped)."""
    chars = re.sub(r"[^0-9A-Za-z]", "", raw)
    if len(chars) <= keep:
        return MASK * len(chars)
    return MASK * (len(chars) - keep) + chars[-keep:]


def mask_email_value(raw: str) -> str:
    local, sep, domain = raw.partition("@")
    if not sep or not local:
        return mask_pii_text(raw)
    return f"{local[0]}{MASK * 3}@{domain}"


def mask_phone_value(raw: str) -> str:
    """``+60123456789`` -> ``+60•••••6789``; ``0123456789`` -> ``01••••6789``.

    Keeps the country code (``+`` and two digits) or the two-digit national
    prefix, and the last 4. Too short to be a number: left alone.
    """
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 6:  # noqa: PLR2004 -- not a phone number
        return raw
    head = ("+" if raw.strip().startswith("+") else "") + digits[:2]
    return f"{head}{MASK * (len(digits) - 2 - _KEEP)}{digits[-_KEEP:]}"


def mask_ic_value(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) < _MIN_IC_TAIL:
        return MASK * len(digits)
    return f"******-**-{digits[-_MIN_IC_TAIL:]}"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:  # noqa: PLR2004
                d -= 9
        total += d
    return total % 10 == 0


def _valid_ic_date(yymmdd: str, pb: str) -> bool:
    month, day = int(yymmdd[2:4]), int(yymmdd[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31 and pb != "00"  # noqa: PLR2004


# The look-behinds keep a match from starting inside a longer token
# ("CASE-900101145678", "#900101145678", "ab0123456789") or a URL/query
# string; the look-aheads keep it from ending inside one.
# The IC rule tolerates a LETTER next to it ("IC900101145678") -- people type
# it that way -- but not a digit or an id/URL separator.
_IC_RE = re.compile(r"(?<![\d#/+=.-])(\d{6})(-?)(\d{2})\2(\d{4})(?![\d-])")
# A number inside a tel:/whatsapp:/wa.me link (the main phone rule will not
# start after ":" or "/", which is what keeps timestamps and URLs intact).
_URI_PHONE_RE = re.compile(r"(?i)(tel:|whatsapp:|wa\.me/)(\+?\d{9,15})(?!\d)")
_CARD_RE = re.compile(r"(?<![\w#/+=.-])[2-6](?:[ -]?\d){12,18}(?![\w-])")
_DISPLAY_PHONE_RE = re.compile(r"(?<![\w:+/=.#&?-])(?:\+\d|0)[\d\s().-]{7,19}\d(?![\w:/-])")
_VIN_RE = re.compile(r"(?<![A-Za-z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9])")
_PASSPORT_RE = re.compile(r"(?<![A-Za-z0-9])[AHK]\d{8}(?![A-Za-z0-9])")


def _sub_ic(m: re.Match[str]) -> str:
    if not _valid_ic_date(m.group(1), m.group(3)):
        return m.group(0)
    return mask_ic_value(m.group(0))


def _sub_card(m: re.Match[str]) -> str:
    raw = m.group(0)
    digits = re.sub(r"\D", "", raw)
    if not _luhn_ok(digits):
        return raw
    return _tail(digits)


def _phone_ok(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    return _MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS and not _DATE_SHAPE.search(raw)


def _sub_phone(m: re.Match[str]) -> str:
    raw = m.group(0)
    if _phone_ok(raw):
        return mask_phone_value(raw)
    if _DATE_SHAPE.search(raw):
        return raw
    # Too many digits: the greedy match ran two numbers together
    # ("0123456789 0198765432"). Take the longest valid number ending at a
    # space, mask it, and carry on with the rest -- never leave both raw.
    for cut in sorted((s.start() for s in re.finditer(r"\s+", raw)), reverse=True):
        head = raw[:cut]
        if _phone_ok(head):
            return mask_phone_value(head) + _DISPLAY_PHONE_RE.sub(_sub_phone, raw[cut:])
    return raw


def _sub_vin(m: re.Match[str]) -> str:
    raw = m.group(0)
    letters = sum(c.isalpha() for c in raw)
    if letters < 2 or len(raw) - letters < 2:  # noqa: PLR2004 -- a word or a number, not a VIN
        return raw
    return _tail(raw)


DEFAULT_RULES: tuple[PiiRule, ...] = (
    # Order matters: e-mail first (its local part may hold digits), then the
    # fixed-shape national ids before the looser phone rule.
    PiiRule("email", "E-mail address", _EMAIL),
    PiiRule("mykad_ic", "MyKad / IC number (YYMMDD-PB-###G)", _IC_RE),
    PiiRule("card_luhn", "Payment card number (Luhn-valid)", _CARD_RE),
    PiiRule("phone", "Phone number (+<country code> or 0 prefix)", _DISPLAY_PHONE_RE),
    PiiRule("vin_chassis", "Vehicle chassis / VIN (17 chars)", _VIN_RE),
    PiiRule("passport_my", "Malaysian passport number", _PASSPORT_RE),
)

_SUBSTITUTIONS = {
    "email": lambda m: mask_email_value(m.group(0)),
    "mykad_ic": _sub_ic,
    "card_luhn": _sub_card,
    "phone": _sub_phone,
    "vin_chassis": _sub_vin,
    "passport_my": lambda m: _tail(m.group(0)),
}


def mask_pii_text(value: str) -> str:
    """Apply every display rule to a free-text string."""
    for rule in DEFAULT_RULES:
        value = rule.pattern.sub(_SUBSTITUTIONS[rule.name], value)
        if rule.name == "phone":
            value = _URI_PHONE_RE.sub(lambda m: m.group(1) + mask_phone_value(m.group(2)), value)
    return value


# Key-based rules: the field NAME says what the value is. Token-anchored so
# "pic" is not an IC field and "province" is not a VIN field.
_KEY_RULES: tuple[tuple[re.Pattern[str], Any], ...] = (
    (re.compile(r"e-?mail", re.I), mask_email_value),
    (
        re.compile(
            r"(?:^|[_\s-])(?:ic|nric|mykad|new_ic|old_ic|ic_no|ic_number|national_id|"
            r"identity_(?:no|number|card))(?:$|[_\s-])",
            re.I,
        ),
        mask_ic_value,
    ),
    (re.compile(r"passport", re.I), _tail),
    (re.compile(r"(?:^|_)vin(?:$|_)|chassis", re.I), _tail),
    (re.compile(r"bank|account_(?:no|number)|acct_?no|card_(?:no|number)", re.I), _tail),
    (re.compile(r"phone|mobile|whatsapp|msisdn|(?:^|_)tel(?:$|_)", re.I), mask_phone_value),
)
# Chatwoot's contact_inboxes.source_id is the bare phone number on a WhatsApp
# or SMS inbox (and an opaque id elsewhere): masked only when digit-only.
_SOURCE_ID_KEY = "source_id"
_BARE_PHONE = re.compile(r"^\+?\d{9,15}$")


def _mask_keyed(key: str, value: str) -> str | None:
    if key == _SOURCE_ID_KEY:
        return mask_phone_value(value) if _BARE_PHONE.match(value) else None
    for pattern, fn in _KEY_RULES:
        if pattern.search(key):
            return fn(value)
    return None


def mask_pii(value: Any, _key: str = "") -> Any:
    """Deep copy of ``value`` with personal identifiers masked for display."""
    if isinstance(value, dict):
        return {k: mask_pii(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_pii(v, _key) for v in value]
    if isinstance(value, str) and value:
        keyed = _mask_keyed(_key, value) if _key else None
        return keyed if keyed is not None else mask_pii_text(value)
    return value


def contains_mask(value: Any) -> bool:
    """True when a value carries the display mask, i.e. it came from a masked
    response and must not be written back over the real value."""
    if isinstance(value, str):
        return MASK in value or "******-**-" in value
    if isinstance(value, dict):
        return any(contains_mask(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_mask(v) for v in value)
    return False
