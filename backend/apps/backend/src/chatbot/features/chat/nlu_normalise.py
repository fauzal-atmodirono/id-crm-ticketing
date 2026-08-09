"""Query-side normaliser for SMS-register Malaysian Malay (P7 task 6).

**Global constraint (do not violate this):** `normalise()` may be applied to
the **retrieval query only** -- the string that becomes an embedding for
FAQ/KB cosine search. It must **never** touch the text handed to the Gemini
model (the customer's turn) or the text written to any agent-facing surface
(e.g. `/kb/suggest`'s echoed `query` field). The register cues in something
like "brp lama siap? nk service" are exactly what make the bot's reply read
like it was written by a person who talks the way the customer does --
strip them from what the model/agent sees and the reply comes back in stiff
textbook Malay to someone who typed in shorthand. This module only ever
*returns* a normalised string; callers decide, per call site, whether that
string is allowed anywhere near the model or an agent-facing surface. The
one production call site that uses this module
(`adapters/merged_knowledge.py::MergedKnowledgeAdapter.search_kb`) applies it
solely to the copy fed to embedding-driven retrieval, never to the raw query
it also threads through to the base/Vertex-Search branch.

**Ship gate.** `NORMALISE_RETRIEVAL_QUERY_ENABLED` is a plain module
constant, not a `Settings` field or env var -- P7 task 6 was explicitly
scoped to add no config/env surface. It defaults to `False` because this
normaliser's acceptance gate ("ship only if it measurably improves task 5's
corpus pass rate") cannot be evaluated in this environment: there is no real
Gemini/Vertex credential here, only a stub (see `test_malay_sms_corpus.py`
and `test_nlu_normalise.py::test_the_corpus_pass_rate_improves_or_the_normaliser_is_not_shipped`).
Flipping it to `True` is conditional on the item recorded in
`docs/analysis/2026-08-09-blocked-work-register.md` (someone running the
corpus against real credentials both ways and confirming an improvement).

**Design: an abbreviation table, not a general speller.** Two conservative
transforms only:

1. Collapse a run of 3+ identical letters to one (`"lamaaaa"` -> `"lama"`,
   `"servisssss"` -> `"servis"`). Legitimate Malay doubled letters are never
   three-in-a-row, so this never touches them.
2. Expand a fixed, whole-word (never substring) table of SMS-register
   abbreviations sourced from P7 task 5's 56-case Malay SMS corpus
   (`fixtures/malay_sms_corpus.json`) -- every key below was observed in a
   case tagged `"abbreviation"` or `"dropped_vowel"` in that corpus, not
   invented. Matching is whole-word (`\\b...\\b`) so a product/model code
   like `"e.mas7"`, `"X50"`, or `"e.MAS 7"` is never touched: the period in
   "e.mas7" and the digits in "X50"/"e.mas7" both break the word boundary a
   plain abbreviation key would need to match on, so those tokens pass
   through unchanged. An unrecognised token is always left exactly as-is --
   this is deliberately not a fuzzy speller.
"""

from __future__ import annotations

import re

# Sourced from P7 task 5's malay_sms_corpus.json -- every key here appears,
# whole-word, in at least one case tagged "abbreviation" or "dropped_vowel"
# in that corpus. Expansions are taken from each case's own `gloss_en`.
_ABBREVIATIONS: dict[str, str] = {
    "brp": "berapa",  # how much/many
    "nk": "nak",  # want
    "utk": "untuk",  # for
    "sy": "saya",  # I/me
    "blh": "boleh",  # can/may
    "leh": "boleh",  # can/may (bare form, e.g. "bila leh siap")
    "dh": "dah",  # already
    "dkt": "dekat",  # near
    "kt": "kat",  # at/in (colloquial for "di")
    "tp": "tapi",  # but
    "tlg": "tolong",  # please/help
    "xleh": "tak boleh",  # cannot
    "xde": "tak ada",  # there isn't/none
    "xtau": "tak tahu",  # don't know
    "xsopan": "tak sopan",  # rude
    "xselesa": "tak selesa",  # uncomfortable
    "xkeluar": "tak keluar",  # doesn't come out
    "xtunjuk": "tak tunjuk",  # doesn't show
    "xsampai": "tak sampai",  # hasn't arrived
    "x": "tak",  # not / "...or not?" question particle (standalone token only)
    "lg": "lagi",  # more/yet
    "blm": "belum",  # not yet
    "ptg": "petang",  # evening/afternoon
    "kete": "kereta",  # car
    "keta": "kereta",  # car (alternate dropped-vowel spelling in the corpus)
    "ade": "ada",  # there is/have (colloquial spelling)
    "yg": "yang",  # that/which
    "dgn": "dengan",  # with
    "sgt": "sangat",  # very
    "je": "saja",  # just/only
    "skrg": "sekarang",  # now
    "tggu": "tunggu",  # wait
    "camne": "macam mana",  # how
    "mcm": "macam",  # like/how
    "org": "orang",  # people
    "bln": "bulan",  # month
    "plg": "paling",  # most
    "lmbt": "lambat",  # late/slow
    "tuka": "tukar",  # change
}

# Longest-key-first so a multi-char key is never shadowed by a shorter one
# under alternation (not currently possible with this table's keys, but kept
# for safety if the table grows).
_ABBREVIATION_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_ABBREVIATIONS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# 3+ identical letters in a row collapse to one. Scoped to letters only (not
# punctuation/whitespace) and to runs of *three or more* specifically so a
# legitimate doubled letter (at most two in a row) is never touched.
_REPEATED_CHAR_PATTERN = re.compile(r"([a-zA-Z])\1{2,}")

# Ships disabled. See module docstring "Ship gate" and
# docs/analysis/2026-08-09-blocked-work-register.md for the exact condition
# under which this may be flipped to True.
NORMALISE_RETRIEVAL_QUERY_ENABLED = False


def _collapse_repeated_characters(text: str) -> str:
    return _REPEATED_CHAR_PATTERN.sub(r"\1", text)


def _expand_abbreviations(text: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        return _ABBREVIATIONS[match.group(1).lower()]

    return _ABBREVIATION_PATTERN.sub(_sub, text)


def normalise(text: str) -> str:
    """Normalise SMS-register Malay for retrieval matching only.

    Never call this on text destined for the model or for any agent-facing
    surface -- see the module docstring's global constraint. Returns a new
    string; `text` itself is never mutated (Python strings are immutable, so
    this is automatic, but it is the property every call site relies on).
    """
    if not text:
        return text
    collapsed = _collapse_repeated_characters(text)
    return _expand_abbreviations(collapsed)
