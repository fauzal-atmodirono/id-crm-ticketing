"""Proton vocabulary and language rules for the post-call transcriber.

**Why.** The transcriber hears an 8 kHz phone line. Without being told the
domain, a general model writes "e-mas seven" as "a mass seven", turns "X70"
into "ex seventy" or a Chinese homophone, invents a town for a dealer it has
never heard of, and -- the failure operators actually complained about --
fills line noise with Thai, Korean or Devanagari characters because nothing
told it which scripts are plausible. Naming the vocabulary and the four
languages Proton's callers actually use fixes the common case for the price of
a few hundred prompt tokens.

**Dealer names** come from the operator-maintained `DealerStore` (the same
records the escalation routing admin edits), so a dealer added there is in the
next transcription's glossary without a deploy. They are cached for a few
minutes -- a Firestore list per call would be a read for every finished call
-- and fail open: a store that cannot be read leaves the static glossary, never
a failed transcription.

`configure_dealer_source` is called once at boot from `main.py` with the shared
store. It is a module-level hook rather than a parameter because
`process_recording`'s call sites live on the phone call path, which this
feature deliberately does not touch.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

# Models, brands and programmes callers say out loud. Order is irrelevant to
# the model; kept readable for the operator reviewing the prompt.
PROTON_GLOSSARY: tuple[str, ...] = (
    "Proton",
    "e.MAS",
    "e.MAS 7",
    "X50",
    "X70",
    "X90",
    "S70",
    "Saga",
    "Persona",
    "Iriz",
    "Exora",
    "PRO-NET",
    "RSA",
    "Petaling Jaya",
)

_DEALER_CACHE_SECONDS = 300.0

_dealer_source: Any = None
_cache: tuple[float, list[str]] | None = None


def configure_dealer_source(store: Any) -> None:
    """Point the glossary at a store with an async `list_all()` returning
    records with a `.dealer` slug. `None` disables dealer lookup."""
    global _dealer_source, _cache  # noqa: PLW0603 -- one boot-time hook, see module docstring
    _dealer_source = store
    _cache = None


def dealer_display_name(slug: str) -> str:
    """`petaling_jaya` / `dealer_shah-alam` -> `Petaling Jaya` / `Shah Alam`,
    the way a caller says it."""
    text = str(slug or "").strip().lower().removeprefix("dealer_")
    words = [w for w in text.replace("-", " ").replace("_", " ").split() if w]
    return " ".join(w.capitalize() for w in words)


async def _dealer_names() -> list[str]:
    global _cache  # noqa: PLW0603
    if _dealer_source is None:
        return []
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _DEALER_CACHE_SECONDS:
        return list(_cache[1])
    try:
        records = await _dealer_source.list_all()
        names = [dealer_display_name(getattr(r, "dealer", "")) for r in records or []]
        names = [n for n in names if n]
    except Exception as exc:
        _log.warning("recording_glossary_dealer_read_failed", error=str(exc))
        return []
    _cache = (now, names)
    return list(names)


async def glossary_terms() -> list[str]:
    """Static glossary plus store dealer names, de-duplicated (case-blind),
    static terms first."""
    seen: set[str] = set()
    out: list[str] = []
    for term in [*PROTON_GLOSSARY, *await _dealer_names()]:
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


def build_transcribe_prompt(terms: Iterable[str]) -> str:
    glossary = ", ".join(terms)
    return (
        "This is a recording of a Proton (Malaysian car maker) customer support "
        "phone call. It has two sides: the customer and the support side (which "
        "may be an AI assistant for part of the call and a human agent for the "
        "rest). Transcribe the entire call verbatim. Label every turn as "
        "CUSTOMER: or AGENT:.\n\n"
        "Languages: Malay, English, Mandarin, Tamil; code-switching is normal, "
        "often within one sentence. Write each passage in the language actually "
        "spoken and do NOT translate. Use Latin script for Malay and English, "
        "Chinese characters for Mandarin and Tamil script for Tamil; never "
        "output other scripts for noise -- line noise, hold music, breathing and "
        "crosstalk are not speech. Mark unclear speech [inaudible] rather than "
        "guessing at words.\n\n"
        "Proton vocabulary (spell these exactly as written when you hear them; "
        "they are model names, dealer locations and programme names): "
        f"{glossary}."
    )
