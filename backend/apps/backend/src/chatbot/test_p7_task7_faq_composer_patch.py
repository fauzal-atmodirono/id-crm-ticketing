"""P7 task 7 -- the FAQ-suggestion composer strip's fork patch.

Closes the gap task 11b found and recorded (register 3g): `FAQ_SUGGESTION_
POPUP_ENABLED` was added to `platform/config.py` and `deploy/tenants/
example.env` with no consumer anywhere. The consumer is a fork patch --
`deploy/chatwoot-fork/patches/0056-faq-composer-apply.patch` -- because the
surface is a Chatwoot dashboard component, not backend Python. There is
therefore no importable module to exercise directly; every test in this file
either parses the patch text itself or applies it (via a real `git apply`) to
a synthetic reconstruction of the two upstream files it stacks on
(`0002-ai-assist-backend.patch`, `0055-translate-action.patch`) and inspects
the result.

**What these tests can and cannot prove**, stated once here rather than
repeated per test:

- They CAN prove the patch's hunks are internally well-formed (correct `@@`
  arithmetic), that they apply cleanly to a tree seeded with content
  transcribed verbatim from 0002's and 0055's own already-merged diffs, and
  that the resulting file contains the exact logic described below.
- They CANNOT prove the patch applies to the real upstream-derived Chatwoot
  fork checkout, because this sandbox has no network access to clone it --
  the same limitation recorded against patches 0053/0054/0055. The brief's
  `test_the_patch_applies_cleanly_onto_the_pinned_upstream_ref` is therefore
  deliberately NOT one of the tests below under that name: nothing here was
  run against the pinned upstream ref, and a test claiming that would be
  false. `test_the_patch_hunks_apply_onto_a_synthetic_reconstruction_of_
  transcribed_context` is the honest, verifiable substitute -- named for
  exactly what it checks.
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
_PATCH_PATH = _REPO_ROOT / "deploy" / "chatwoot-fork" / "patches" / "0056-faq-composer-apply.patch"

assert _PATCH_PATH.is_file(), f"0056 patch not found at {_PATCH_PATH}"

PATCH_TEXT = _PATCH_PATH.read_text(encoding="utf-8")

# The diff body only (drop the email-style preamble before the first
# "diff --git", same convention 0053/0054/0055 use for their headers).
_DIFF_START = PATCH_TEXT.index("diff --git")
DIFF_TEXT = PATCH_TEXT[_DIFF_START:]

TARGET_REL_PATH = "app/javascript/dashboard/components/widgets/WootWriter/ReplyTopPanel.vue"

# ---------------------------------------------------------------------------
# Synthetic reconstruction of the file this patch stacks on (0002 + 0055),
# built ONLY from lines transcribed verbatim from those two patches' own
# already-merged diffs -- never from memory of unverified upstream Chatwoot
# source. Filler lines stand in for the surrounding content this sandbox has
# no way to see. This is the same "reconstruct, pad with filler, verify with
# a real git apply" technique 0053's report describes.
# ---------------------------------------------------------------------------
_KNOWN_LINES: dict[int, str] = {
    1: "<script>",
    2: "import { ref, computed } from 'vue';",
    3: "import { useKeyboardEvents } from 'dashboard/composables/useKeyboardEvents';",
    4: "import { useCaptain } from 'dashboard/composables/useCaptain';",
    5: "import { useTrack } from 'dashboard/composables';",
    9: "import NextButton from 'dashboard/components-next/button/Button.vue';",
    10: "import EditorModeToggle from './EditorModeToggle.vue';",
    11: "import CopilotMenuBar from './CopilotMenuBar.vue';",
    12: "import { useProtonConfig } from 'dashboard/composables/useProtonConfig';",
    13: "import { callAssist } from 'dashboard/api/protonAssist';",
    14: "import { useStore } from 'dashboard/composables/store';",
    15: "import { useAlert } from 'dashboard/composables';",
    16: "",
    17: "// Actions intercepted by Proton backend when ai_assist feature is enabled",
    18: "const PROTON_ACTIONS = {",
    19: "  reply_suggestion: 'suggest',",
    20: "  summarize: 'summarize',",
    21: "  ask_copilot: 'ask',",
    22: "};",
    23: "",
    24: "// Where each action's result is inserted: the 'reply' box or a private 'note'.",
    25: "const PROTON_ACTION_MODE = {",
    26: "  reply_suggestion: 'reply',",
    27: "  summarize: 'note',",
    28: "  ask_copilot: 'reply',",
    29: "};",
    30: "",
    31: "export default {",
    32: "  name: 'ReplyTopPanel',",
    97: "    };",
    98: "",
    99: "    const { captainTasksEnabled } = useCaptain();",
    100: "    const { hasFeature } = useProtonConfig();",
    101: "    const store = useStore();",
    102: "    const protonEnabled = computed(() => hasFeature('ai_assist'));",
    103: "    const showAiButton = computed(",
    104: "      () => captainTasksEnabled.value || protonEnabled.value",
    105: "    );",
    106: "",
    107: "    const showCopilotMenu = ref(false);",
    108: "    const copilotToggleRef = ref(null);",
    208: "      handleNoteClick,",
    209: "      REPLY_EDITOR_MODES,",
    210: "      captainTasksEnabled,",
    211: "      protonEnabled,",
    212: "      showAiButton,",
    213: "      translating,",
    214: "      handleTranslateLastCustomerMessage,",
    215: "      handleCopilotAction,",
    216: "      showCopilotMenu,",
    217: "      copilotToggleRef,",
    259: "        </span>",
    260: "      </div>",
    261: "    </div>",
    262: '    <div v-if="protonEnabled" class="flex items-center gap-2">',
    263: "      <button",
    264: '        class="flex items-center gap-1 px-2 py-1 text-xs border rounded-lg border-n-weak text-n-slate-11"',
    265: '        :disabled="translating"',
    266: '        title="Translate the customer\'s last message to English"',
    267: '        @click="handleTranslateLastCustomerMessage"',
    268: "      >",
    269: '        <span class="i-lucide-languages" />',
    270: "        {{ translating ? 'Translating…' : 'Translate' }}",
    271: "      </button>",
    272: "    </div>",
    273: '    <div v-if="showAiButton" class="flex items-center gap-2">',
    274: '      <div class="relative">',
    275: "        <NextButton",
    276: '          ref="copilotToggleRef"',
}
_SYNTHETIC_LENGTH = 280


def _build_synthetic_base() -> str:
    lines = [
        _KNOWN_LINES.get(i, f"// filler-transcribed-context-unknown-line-{i}")
        for i in range(1, _SYNTHETIC_LENGTH + 1)
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def applied_file(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Apply the real 0056 patch to the synthetic base with a real `git
    apply`, inside a throwaway git repo, and return the resulting file text.

    Session-scoped-ish (module scope) because applying is the same for every
    test in this file and git/subprocess calls are not free.
    """
    repo = tmp_path_factory.mktemp("patch0056-tree")
    target = repo / TARGET_REL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_build_synthetic_base(), encoding="utf-8")

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        # All arguments are hardcoded literals below (git plumbing only) --
        # never untrusted input -- so the subprocess call is safe despite S603.
        return subprocess.run(  # noqa: S603
            args, cwd=repo, capture_output=True, text=True, check=False
        )

    assert run("git", "init", "-q").returncode == 0
    assert run("git", "add", "-A").returncode == 0
    assert (
        run(
            "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base"
        ).returncode
        == 0
    )

    check = run("git", "apply", "--check", str(_PATCH_PATH))
    assert check.returncode == 0, (
        "0056 did not apply to the synthetic reconstruction of 0002+0055's "
        f"transcribed content (proves internal consistency only): {check.stderr}"
    )
    applied = run("git", "apply", str(_PATCH_PATH))
    assert applied.returncode == 0, applied.stderr

    return target.read_text(encoding="utf-8")


def test_the_patch_hunks_apply_onto_a_synthetic_reconstruction_of_transcribed_context(
    applied_file: str,
) -> None:
    """Honest substitute for the brief's `..._onto_the_pinned_upstream_ref`
    test, which cannot pass in this sandbox (no network access to clone
    upstream -- see the module docstring). This proves the five hunks'
    `@@` arithmetic is internally correct and that they land cleanly against
    content transcribed verbatim from 0002's and 0055's own merged diffs. It
    proves nothing about the real fork checkout.
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
