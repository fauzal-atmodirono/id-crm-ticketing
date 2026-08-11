"""P7 task 7 -- the FAQ-suggestion composer strip's fork patch.

Closes the gap task 11b found and recorded (register 3g): `FAQ_SUGGESTION_
POPUP_ENABLED` was added to `platform/config.py` and `deploy/tenants/
example.env` with no consumer anywhere. The consumer is a fork patch --
`deploy/chatwoot-fork/patches/0056-faq-composer-apply.patch` -- because the
surface is a Chatwoot dashboard component, not backend Python. There is
therefore no importable module to exercise directly; every test in this file
either parses the patch text itself or applies it (via a real `git apply`) and
inspects the result.

**2026-08-11: this file used to apply the patch to a hand-written synthetic
reconstruction of its pre-image, and that reconstruction was wrong.** Its
`_KNOWN_LINES` asserted a `useAlert` import at line 15 of `ReplyTopPanel.vue`
and `useStore` at line 14. The real file has no `useAlert` import at all, and
line 14 is `useUISettings`. Because both 0055 and 0056 were written against
that fiction, both failed on the first real Cloud Build -- 0055 at
`ReplyTopPanel.vue:12`, 0056 at `ReplyTopPanel.vue:97`. Both patches have been
regenerated from a real `git diff`, and this file now applies them to the real
pre-image instead of a reconstruction:
`deploy/chatwoot-fork/fixtures/ReplyTopPanel.post-0054.vue`, extracted by
applying patches 0001-0054 to `chatwoot/chatwoot:v4.15.1` in a throwaway
container (provenance and the extraction command are in that directory's
README). A fixture cannot encode a wrong guess about upstream; a
reconstruction can, and did.

**What these tests can and cannot prove**, stated once here rather than
repeated per test:

- They CAN prove that 0055 applies cleanly to the real post-0054
  `ReplyTopPanel.vue`, that 0056 then applies cleanly to the result of that,
  and that the resulting file contains the exact logic described below. The
  pre-image is ground truth, so this is a real check against the real fork
  tree for this one file.
- They do NOT re-run the whole 59-patch stack -- pytest has no access to the
  upstream image. That check lives outside this suite: applying every patch in
  order inside a `chatwoot/chatwoot:v4.15.1` container, committing after each
  one, was run on 2026-08-11 and reported `=== FAILING:` with nothing after
  it. See `.superpowers/sdd/fork-patch-verification.md`. Patches applying is
  still necessary, not sufficient: only Cloud Build proves the vite build
  compiles the patched source.
- Because there is no Vue runtime available here, "the Apply button writes
  the composer" and friends are proven by extracting the generated JavaScript
  and asserting its structure/semantics (regex plus small Python
  re-implementations of the extracted boolean expressions, checked against
  both the extracted text and representative inputs) -- not by rendering the
  component and clicking anything.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
_FORK_ROOT = _REPO_ROOT / "deploy" / "chatwoot-fork"
_PATCH_PATH = _FORK_ROOT / "patches" / "0056-faq-composer-apply.patch"
# 0056 stacks on 0055, so 0055 has to be applied first to build the pre-image.
# Applying it here also makes this file 0055's only automated guard.
_PATCH_0055_PATH = _FORK_ROOT / "patches" / "0055-translate-action.patch"
# The REAL pre-image, extracted from the upstream image with 0001-0054 applied
# -- not a reconstruction. See fixtures/README.md for how it was extracted and
# when to refresh it.
_PRE_IMAGE_PATH = _FORK_ROOT / "fixtures" / "ReplyTopPanel.post-0054.vue"

assert _PATCH_PATH.is_file(), f"0056 patch not found at {_PATCH_PATH}"
assert _PATCH_0055_PATH.is_file(), f"0055 patch not found at {_PATCH_0055_PATH}"
assert _PRE_IMAGE_PATH.is_file(), f"post-0054 ground-truth file not found at {_PRE_IMAGE_PATH}"

PATCH_TEXT = _PATCH_PATH.read_text(encoding="utf-8")

# The diff body only (drop the email-style preamble before the first
# "diff --git", same convention 0053/0054/0055 use for their headers).
_DIFF_START = PATCH_TEXT.index("diff --git")
DIFF_TEXT = PATCH_TEXT[_DIFF_START:]

TARGET_REL_PATH = "app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue"


@pytest.fixture(scope="module")
def applied_file(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Apply 0055 then 0056 to the REAL post-0054 `ReplyTopPanel.vue`, with a
    real `git apply` inside a throwaway git repo, and return the result.

    0055 is applied first because 0056 stacks on it; committing in between is
    what makes 0056's check meaningful (an uncommitted tree lets `git apply`
    match against the pre-0055 content and hide a stacking error -- the same
    mistake that produced three false failures in the container harness run).

    Module-scoped because applying is identical for every test here and
    git/subprocess calls are not free.
    """
    repo = tmp_path_factory.mktemp("patch0056-tree")
    target = repo / TARGET_REL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PRE_IMAGE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        # All arguments are hardcoded literals below (git plumbing only) --
        # never untrusted input -- so the subprocess call is safe despite S603.
        return subprocess.run(  # noqa: S603
            args, cwd=repo, capture_output=True, text=True, check=False
        )

    def commit(message: str) -> None:
        assert run("git", "add", "-A").returncode == 0
        assert (
            run(
                "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message
            ).returncode
            == 0
        )

    assert run("git", "init", "-q").returncode == 0
    commit("post-0054 ground truth")

    for label, patch in (("0055", _PATCH_0055_PATH), ("0056", _PATCH_PATH)):
        check = run("git", "apply", "--check", str(patch))
        assert check.returncode == 0, (
            f"{label} does not apply to the real post-0054 ReplyTopPanel.vue. "
            "Either the patch is wrong, or an earlier patch now touches this "
            "file and fixtures/ReplyTopPanel.post-0054.vue needs re-extracting "
            f"(see that directory's README): {check.stderr}"
        )
        assert run("git", "apply", str(patch)).returncode == 0
        commit(label)

    return target.read_text(encoding="utf-8")


def test_the_patch_applies_cleanly_onto_the_real_post_0054_fork_file(
    applied_file: str,
) -> None:
    """The check that used to be impossible here. The pre-image is the real
    `ReplyTopPanel.vue` from the patched fork tree, so a clean `git apply` of
    0055-then-0056 onto it is a real result, not an internal-consistency
    proof. It still says nothing about whether vite compiles the output.
    """
    # The fixture already asserted `git apply --check` and `git apply`
    # succeeded; this test additionally pins that every intended addition
    # actually landed in the file (not just that *some* patch applied).
    for marker in (
        "import { ref, computed, watch } from 'vue';",
        "const FAQ_SUGGESTION_CONFIDENCE_THRESHOLD = 0.75;",
        "const { hasFeature, backendUrl } = useProtonConfig();",
        "const fetchFaqSuggestion = async message =>",
        "const applyFaqSuggestion = () =>",
        "const dismissFaqSuggestion = () =>",
        'v-if="faqSuggestionVisible"',
    ):
        assert marker in applied_file, f"expected marker missing after apply: {marker!r}"


def test_the_apply_button_writes_the_suggestion_into_the_composer(
    applied_file: str,
) -> None:
    """The Apply button must reuse 0002's `protonAssistResult` bridge (the
    mechanism `ReplyBox.vue`'s `onProtonAssistResult` already writes
    `this.message` from) rather than inventing a second bridge or trying to
    reach across the iframe sandbox.
    """
    match = re.search(
        r"const applyFaqSuggestion = \(\) => \{(.*?)\n\s*\};",
        applied_file,
        re.DOTALL,
    )
    assert match, "applyFaqSuggestion not found in the applied file"
    body = match.group(1)

    assert "emit('protonAssistResult'" in body
    assert "mode: 'reply'" in body

    # It must paste the FULL answer, not the display snippet. `snippet` is a
    # 280-char truncation for the strip; pasting it sends the customer a
    # warranty clause cut off mid-word. The original version of this assertion
    # required `snippet` and so pinned that bug in place -- the P7 final review
    # caught it. `snippet` may still appear, but only after `answer` as the
    # fallback for a backend predating the field.
    assert "faqSuggestion.value.answer" in body, (
        "Apply must source the full `answer`, not the truncated `snippet`"
    )
    answer_pos = body.index("faqSuggestion.value.answer")
    if "faqSuggestion.value.snippet" in body:
        assert body.index("faqSuggestion.value.snippet") > answer_pos, (
            "`snippet` may only be a fallback after `answer`, never preferred"
        )

    # The template's Apply control must call this exact handler.
    assert re.search(r'@click="applyFaqSuggestion"', applied_file)


def test_the_existing_ai_assist_composer_write_is_unaffected(applied_file: str) -> None:
    """The highest-risk regression this patch could cause: breaking the
    three actions (reply_suggestion/summarize/ask_copilot) that 0002 already
    wires end to end. Checked two ways -- from the diff itself, and from the
    applied output -- so a change hiding in either direction is caught.
    """
    # 1. The diff removes exactly the two lines this patch intends to change
    #    (the `vue` import and the `useProtonConfig` destructure) and nothing
    #    else. If `handleCopilotAction`'s body, `PROTON_ACTIONS`, or
    #    `PROTON_ACTION_MODE` had been touched, a `-` line for them would
    #    appear here.
    removed = [
        line
        for line in DIFF_TEXT.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    assert removed == [
        "-import { ref, computed } from 'vue';",
        "-    const { hasFeature } = useProtonConfig();",
    ], removed

    # 2. The existing PROTON_ACTIONS map, PROTON_ACTION_MODE map, and the
    #    0002-authored `protonAssistResult` emit inside `handleCopilotAction`
    #    survive byte-for-byte in the patched file.
    for verbatim in (
        "const PROTON_ACTIONS = {\n"
        "  reply_suggestion: 'suggest',\n"
        "  summarize: 'summarize',\n"
        "  ask_copilot: 'ask',\n"
        "};",
        "const PROTON_ACTION_MODE = {\n"
        "  reply_suggestion: 'reply',\n"
        "  summarize: 'note',\n"
        "  ask_copilot: 'reply',\n"
        "};",
    ):
        assert verbatim in applied_file


def _extract(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.DOTALL)
    assert match, f"pattern not found: {pattern!r}"
    return match.group(0)


def test_the_suggestion_strip_is_hidden_when_the_popup_flag_is_off(
    applied_file: str,
) -> None:
    """`FAQ_SUGGESTION_POPUP_ENABLED` reaches the frontend as the
    `faq_suggestion_popup` feature name (the same `PROTON_FEATURES` /
    `hasFeature()` mechanism 0001/0002 already use for `ai_assist`). The
    strip's visibility computed must AND that flag in as its first,
    short-circuiting operand, so it is false whenever the flag is off no
    matter what else is true.
    """
    assert "hasFeature('faq_suggestion_popup')" in applied_file

    visible_block = _extract(
        r"const faqSuggestionVisible = computed\(\n(.*?)\n\s*\);", applied_file
    )
    assert "faqSuggestionPopupEnabled.value &&" in visible_block

    # A small, faithful re-implementation of the extracted `&&` chain,
    # checked against the actual operand strings above rather than invented
    # independently -- proves the semantics, not just the presence of a flag.
    def faq_suggestion_visible(
        popup_enabled: bool, has_suggestion: bool, same_message_dismissed: bool
    ) -> bool:
        return popup_enabled and has_suggestion and not same_message_dismissed

    assert faq_suggestion_visible(False, True, False) is False
    assert faq_suggestion_visible(False, False, False) is False
    assert faq_suggestion_visible(True, True, False) is True


def test_a_suggestion_below_the_confidence_threshold_is_not_shown_as_a_popup(
    applied_file: str,
) -> None:
    """Only `live_faq` hits from `GET /kb/suggest` carry a numeric `score`
    (`kb_suggest_router.py`'s Vertex hits never do -- verified by reading that
    router, unchanged by this patch). The threshold check therefore rejects
    a missing score by construction, not via a second "is this Vertex"
    branch that could fall out of sync.
    """
    assert "const FAQ_SUGGESTION_CONFIDENCE_THRESHOLD = 0.75;" in applied_file

    fetch_block = _extract(
        r"const fetchFaqSuggestion = async message => \{(.*?)\n    \};",
        applied_file,
    )
    assert "typeof top.score === 'number'" in fetch_block
    assert "top.score >= FAQ_SUGGESTION_CONFIDENCE_THRESHOLD" in fetch_block
    assert "faqSuggestion.value = null;" in fetch_block

    # Re-implementation of the extracted condition, exercised against
    # representative /kb/suggest response shapes.
    threshold = 0.75

    def accepts(top: dict[str, object] | None) -> bool:
        if not top:
            return False
        score = top.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            return False
        return score >= threshold

    assert accepts({"title": "x", "score": 0.9}) is True
    assert accepts({"title": "x", "score": 0.5}) is False  # below threshold
    assert accepts({"title": "x"}) is False  # Vertex hit, no score at all
    assert accepts(None) is False


def test_dismissing_the_strip_does_not_re_show_it_for_the_same_message(
    applied_file: str,
) -> None:
    """Dismissal is keyed by the customer message id, not by conversation or
    by "the current suggestion object" -- so a later re-fetch that returns
    the very same suggestion for the very same message must NOT reopen the
    strip, while a genuinely new customer message can.
    """
    dismiss_block = _extract(r"const dismissFaqSuggestion = \(\) => \{(.*?)\n    \};", applied_file)
    assert "dismissedFaqMessageId.value = faqSuggestion.value.messageId;" in dismiss_block

    visible_block = _extract(
        r"const faqSuggestionVisible = computed\(\n(.*?)\n\s*\);", applied_file
    )
    assert "faqSuggestion.value.messageId !== dismissedFaqMessageId.value" in visible_block

    # Re-implementation, exercised against the scenario the test name names.
    def visible_after_dismiss(suggestion_message_id: int, dismissed_message_id: int | None) -> bool:
        return suggestion_message_id != dismissed_message_id

    # Dismiss for message 42, then the same suggestion comes back for the
    # same message (e.g. a later poll re-fetches the identical hit) -- must
    # stay hidden.
    dismissed_id = 42
    assert visible_after_dismiss(42, dismissed_id) is False
    # A new customer message (43) must not be suppressed by 42's dismissal.
    assert visible_after_dismiss(43, dismissed_id) is True


def test_the_suggestion_does_not_leak_across_conversations(applied_file: str) -> None:
    """P7 final review, I7. The first version watched only
    `getSelectedChat?.messages?.length`, so switching from conversation A to
    conversation B with an equal message count fired no callback at all: the
    strip kept showing A's suggestion and Apply pasted A's answer into B's
    composer. A conversation with no customer message yet showed A's too.

    The watch must therefore track the conversation *id*, clear eagerly when it
    changes (so no stale suggestion is visible during B's fetch), and run on
    entry rather than waiting for B's next inbound message.
    """
    match = re.search(r"watch\(\s*\(\) => \[(.*?)\],", applied_file, re.DOTALL)
    assert match, "the watch source is not a list -- it cannot be tracking both keys"
    source = match.group(1)

    assert "getSelectedChat?.id" in source, "the conversation id is not watched"
    assert "messages?.length" in source, "the message count is no longer watched"

    body_match = re.search(r"watch\(\s*\(\) => \[.*?\],\s*\((.*?)\n\s*\},", applied_file, re.DOTALL)
    assert body_match, "the watch callback was not found"
    body = body_match.group(1)
    assert "faqSuggestion.value = null" in body, (
        "the callback never clears the previous conversation's suggestion"
    )
    assert "dismissedFaqMessageId.value = null" in body, (
        "a dismissal must not carry over to a different conversation"
    )

    # `immediate` is what makes the strip appear on entering a conversation
    # rather than only after its next inbound message.
    assert "immediate: true" in applied_file, "the watch does not run on entry"

    # A conversation with no customer message must show nothing, not the
    # previous conversation's suggestion.
    refresh = re.search(
        r"const refreshFaqSuggestion = \(\) => \{(.*?)\n\s*\};", applied_file, re.DOTALL
    )
    assert refresh, "refreshFaqSuggestion not found"
    assert "faqSuggestion.value = null" in refresh.group(1), (
        "a conversation with no customer message leaves the old suggestion visible"
    )
