"""P9 tasks 2/3/6 -- the fork alert module, its poll fallback, and the
alert-preferences page.

The surface is a Chatwoot fork patch
(`deploy/chatwoot-fork/patches/0057-inbound-alerts.patch`), not backend
Python, so there is no importable module to exercise directly. Every test here
either parses the patch text, applies it (with a real `git apply`) to a
synthetic reconstruction of the two upstream-derived files it modifies, or
EXECUTES the exact JavaScript source text it added, in node.

**What these tests can and cannot prove**, stated once here rather than
repeated per test:

- They CAN prove the patch's hunks are internally well-formed, that they apply
  cleanly to a tree seeded with content transcribed verbatim from 0003's,
  0025's, 0035's, 0041's, 0043's, 0053's and 0054's own already-merged diffs,
  and -- where node is available -- that the shipped decision functions behave
  as described when run against representative inputs. The behavioural tests
  extract the real function source out of the applied file and run it; they do
  not re-implement it, so they cannot pass against a broken implementation.
- They CANNOT prove the patch applies to the real upstream-derived Chatwoot
  fork checkout, because this sandbox has no network access to clone it -- the
  same limitation recorded against patches 0053/0054/0055/0056. The brief's
  `test_the_patch_applies_cleanly_onto_the_pinned_upstream_ref` is therefore
  deliberately NOT one of the tests below under that name: nothing here was
  run against the pinned upstream ref, and a test claiming it would be false.
  `test_the_patch_hunks_apply_onto_a_synthetic_reconstruction_of_transcribed_
  context` is the honest, verifiable substitute -- named for exactly what it
  checks.
- They CANNOT prove the two things the patch guesses about upstream APIs it
  could not read: the Vuex conversation-list getter names, and the shape of
  `GET /api/v1/accounts/:id/conversations`. Both are guarded so a wrong guess
  degrades to a visible 60-second poll rather than to silence -- and *that*
  guard is proven below, since it is the part that has to be right either way.
- They prove nothing about pixels. No Vue runtime and no DOM are available
  here, so "the degraded indicator is shown" is proven at the level of the
  decision that shows it plus the shipped DOM-building code's structure, not
  by rendering a browser window. The manual screenshot verification remains
  owed and is recorded in
  `docs/analysis/2026-08-09-blocked-work-register.md`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
_PATCH_PATH = _REPO_ROOT / "deploy" / "chatwoot-fork" / "patches" / "0057-inbound-alerts.patch"
_MY_TASKS_PATH = _REPO_ROOT / "backend" / "apps" / "chatwoot-my-tasks" / "index.html"

assert _PATCH_PATH.is_file(), f"0057 patch not found at {_PATCH_PATH}"

PATCH_TEXT = _PATCH_PATH.read_text(encoding="utf-8")

# The diff body only (drop the email-style preamble before the first
# "diff --git", the convention 0053-0056's headers use).
DIFF_TEXT = PATCH_TEXT[PATCH_TEXT.index("diff --git") :]

SIDEBAR_REL_PATH = "app/javascript/dashboard/components-next/sidebar/Sidebar.vue"
ROUTES_REL_PATH = "app/javascript/dashboard/routes/dashboard/dashboard.routes.js"
HELPER_REL_PATH = "app/javascript/dashboard/helper/protonAlerts.js"
COMPOSABLE_REL_PATH = "app/javascript/dashboard/composables/useProtonInboundAlerts.js"
API_REL_PATH = "app/javascript/dashboard/api/protonAlerts.js"
PAGE_REL_PATH = "app/javascript/dashboard/views/ProtonAlertPreferencesPage.vue"

# ---------------------------------------------------------------------------
# Synthetic reconstruction of the two files this patch modifies, built ONLY
# from lines transcribed verbatim from already-merged patches' own diffs.
# Filler stands in for the surrounding content this sandbox cannot see -- the
# "reconstruct, pad with labelled filler, verify with a real git apply"
# technique 0053's and 0056's reports describe.
#
# Positions: 0002 added Sidebar's `useProtonConfig` import at line 14 and 0025
# its `useProtonPermissions` import at 15 and `protonHasPermission` at 68;
# nothing after 0025 touches Sidebar.vue above line 617, so those are final.
# 0054's Sidebar hunk fixes the RSA comment block at 660, after which 0035's
# block (15 lines), 0041's (10) and 0043's (15) put `{ name: 'Contacts'` at
# 700. In dashboard.routes.js 0054 fixes the my-status block at 91-96, so
# 0043's cases block is 97-102 and 0003's `...inboxRoutes,` trio 103-105.
# ---------------------------------------------------------------------------
_SIDEBAR_KNOWN: dict[int, str] = {
    11: "import { vOnClickOutside } from '@vueuse/components';",
    12: "import { FEATURE_FLAGS } from 'dashboard/featureFlags';",
    13: "import { useWindowSize, useEventListener } from '@vueuse/core';",
    14: "import { useProtonConfig } from 'dashboard/composables/useProtonConfig';",
    15: "import { useProtonPermissions } from 'dashboard/composables/useProtonPermissions';",
    16: "",
    17: "import Button from 'dashboard/components-next/button/Button.vue';",
    18: "import SidebarGroup from './SidebarGroup.vue';",
    65: "",
    66: "const { hasFeature: protonHasFeature } = useProtonConfig();",
    67: "const protonAiAssistEnabled = computed(() => protonHasFeature('ai_assist'));",
    68: "const { hasPermission: protonHasPermission } = useProtonPermissions();",
    69: "",
    70: "const hasAdvancedAssignment = computed(() => {",
    71: "  return isFeatureEnabledonAccount.value(",
    685: "    // Cases list — reads Chatwoot conversation data directly (see",
    686: "    // dashboard/api/protonCases.js), so there's no backend require_permission",
    687: "    // route to match the way RSA/SLA/escalation have one. Gate on",
    688: "    // `customer360.view`, the nearest fitting existing permission: both are",
    689: "    // read-only CRM case-lookup surfaces for the same operator cohort.",
    690: "    ...(protonHasPermission('customer360.view')",
    691: "      ? [",
    692: "          {",
    693: "            name: 'ProtonCases',",
    694: "            icon: 'i-lucide-list-checks',",
    695: "            label: 'Cases',",
    696: "            to: accountScopedRoute('proton_cases'),",
    697: "          },",
    698: "        ]",
    699: "      : []),",
    700: "    {",
    701: "      name: 'Contacts',",
    702: "      label: t('SIDEBAR.CONTACTS'),",
}
_SIDEBAR_LENGTH = 720

_ROUTES_KNOWN: dict[int, str] = {
    85: "        {",
    86: "          path: 'proton/workforce',",
    87: "          name: 'proton_workforce',",
    88: "          component: () => import('../../views/ProtonWorkforceDashboardPage.vue'),",
    89: "          meta: { permissions: ['administrator'] },",
    90: "        },",
    91: "        {",
    92: "          path: 'proton/my-status',",
    93: "          name: 'proton_my_status',",
    94: "          component: () => import('../../views/ProtonMyStatusPage.vue'),",
    95: "          meta: { permissions: ['administrator', 'agent'] },",
    96: "        },",
    97: "        {",
    98: "          path: 'proton/cases',",
    99: "          name: 'proton_cases',",
    100: "          component: () => import('../../views/ProtonCasesPage.vue'),",
    101: "          meta: { permissions: ['administrator'] },",
    102: "        },",
    103: "        ...inboxRoutes,",
    104: "        ...conversation.routes,",
    105: "        ...settings.routes,",
}
_ROUTES_LENGTH = 115


def _build(known: dict[int, str], length: int) -> str:
    lines = [
        known.get(i, f"// filler-transcribed-context-unknown-line-{i}")
        for i in range(1, length + 1)
    ]
    return "\n".join(lines) + "\n"


def _run(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # All arguments below are hardcoded literals (git plumbing / node) -- never
    # untrusted input -- so the subprocess call is safe despite S603.
    return subprocess.run(  # noqa: S603
        args, cwd=tree, capture_output=True, text=True, check=False
    )


@pytest.fixture(scope="module")
def applied(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Apply the real 0057 patch to the synthetic base with a real `git apply`
    inside a throwaway repo, and return every resulting file's text keyed by
    repo-relative path.
    """
    tree = tmp_path_factory.mktemp("patch0057-tree")
    for rel, content in (
        (SIDEBAR_REL_PATH, _build(_SIDEBAR_KNOWN, _SIDEBAR_LENGTH)),
        (ROUTES_REL_PATH, _build(_ROUTES_KNOWN, _ROUTES_LENGTH)),
    ):
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert _run(tree, "git", "init", "-q").returncode == 0
    assert _run(tree, "git", "add", "-A").returncode == 0
    assert (
        _run(
            tree,
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "base",
        ).returncode
        == 0
    )

    check = _run(tree, "git", "apply", "--check", str(_PATCH_PATH))
    assert check.returncode == 0, (
        "0057 did not apply to the synthetic reconstruction of 0003/0025/0035/"
        f"0041/0043/0053/0054's transcribed content (internal consistency only): {check.stderr}"
    )
    assert _run(tree, "git", "apply", str(_PATCH_PATH)).returncode == 0

    return {
        rel: (tree / rel).read_text(encoding="utf-8")
        for rel in (
            SIDEBAR_REL_PATH,
            ROUTES_REL_PATH,
            HELPER_REL_PATH,
            COMPOSABLE_REL_PATH,
            API_REL_PATH,
            PAGE_REL_PATH,
        )
    }


# ---------------------------------------------------------------------------
# Running the shipped JavaScript
# ---------------------------------------------------------------------------

_NODE = shutil.which("node")

# The helper's pure decision logic ends where the DOM primitives begin. Cutting
# there rather than stubbing `document` keeps the executed text exactly the
# shipped text: everything above this marker touches neither DOM nor network.
_PURE_CUTOFF = "// The primitives, transcribed from apps/chatwoot-my-tasks/index.html"


def _pure_helper_source(helper_text: str) -> str:
    cutoff = helper_text.index(_PURE_CUTOFF)
    return helper_text[:cutoff]


def _node_eval(helper_text: str, script: str, tmp_path: Path) -> object:
    """Run `script` in node with the helper's PURE source in scope, and return
    the JSON it prints. Skips (rather than silently passing) where node is
    unavailable, so a green run always means the JavaScript actually ran.
    """
    if _NODE is None:  # pragma: no cover - environment dependent
        pytest.skip("node is not available; the shipped JavaScript cannot be executed here")
    module = tmp_path / "harness.mjs"
    module.write_text(
        _pure_helper_source(helper_text) + "\n" + script + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(  # noqa: S603
        [_NODE, str(module)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Task 2
# ---------------------------------------------------------------------------


def test_the_patch_hunks_apply_onto_a_synthetic_reconstruction_of_transcribed_context(
    applied: dict[str, str],
) -> None:
    """Honest substitute for the brief's `..._onto_the_pinned_upstream_ref`
    test, which cannot pass in this sandbox (see the module docstring). It
    proves the four hunks' arithmetic is internally correct, that they land
    against content transcribed verbatim from the seven parent patches' merged
    diffs, and that every intended addition actually arrived -- not merely that
    *some* patch applied.
    """
    sidebar = applied[SIDEBAR_REL_PATH]
    assert (
        "import { useProtonInboundAlerts } from 'dashboard/composables/useProtonInboundAlerts';"
        in sidebar
    )
    assert "useProtonInboundAlerts();" in sidebar
    assert "name: 'ProtonAlertPreferences'," in sidebar
    assert "to: accountScopedRoute('proton_alert_preferences')," in sidebar

    routes = applied[ROUTES_REL_PATH]
    assert "path: 'proton/alert-preferences'," in routes
    assert "name: 'proton_alert_preferences'," in routes
    assert "import('../../views/ProtonAlertPreferencesPage.vue')" in routes

    # The four new files landed with content, not as empty stubs.
    for rel in (HELPER_REL_PATH, COMPOSABLE_REL_PATH, API_REL_PATH, PAGE_REL_PATH):
        assert len(applied[rel].splitlines()) > 40, rel


def test_the_gating_flag_has_a_real_consumer_and_the_page_is_routable(
    applied: dict[str, str],
) -> None:
    """A green unit test is not reachability. This pins the three links that
    make the feature reachable at all: the module is INSTALLED from a component
    mounted on every dashboard page, its client-side gate is actually READ, and
    the preferences page has both a route and a nav entry pointing at that
    route's name.
    """
    sidebar = applied[SIDEBAR_REL_PATH]
    composable = applied[COMPOSABLE_REL_PATH]

    # Installed, not merely defined.
    assert re.search(r"^useProtonInboundAlerts\(\);$", sidebar, re.M)
    # The gate is read, and returns early when it is off.
    assert "hasFeature('inbound_alerts')" in composable
    assert re.search(r"if \(!hasFeature\('inbound_alerts'\)\) return", composable)
    # Route name and nav target agree, so the nav entry cannot 404.
    assert "name: 'proton_alert_preferences'," in applied[ROUTES_REL_PATH]
    assert "accountScopedRoute('proton_alert_preferences')" in sidebar
    # The page calls the router's real paths.
    api = applied[API_REL_PATH]
    for path in (
        "'/alerts/rules/mine'",
        "/alerts/rules/mine/${encodeURIComponent(event)}",
        "'/alerts/rules/defaults'",
        "/alerts/rules/defaults/${encodeURIComponent(event)}",
    ):
        assert path in api, path


def test_new_inbound_defaults_to_toast_only(applied: dict[str, str], tmp_path: Path) -> None:
    """A DESIGN ASSERTION, not a detail. On a tenant where WhatsApp carries
    most of the volume, sound on new_inbound is a beep every few seconds, and
    the first thing every agent does is disable ALL alerting -- including the
    sla_breach alerts that actually matter. If someone later "improves" this
    default, this fails and they have to read why.

    Also pins that the capability IS present (sound and desktop are offered for
    the event, just off) -- the requirement is met by configurability, not by
    the loudest default -- and that sla_breach keeps all three.
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        console.log(JSON.stringify({
          new_inbound: DEFAULT_ALERT_RULES.new_inbound,
          sla_breach: DEFAULT_ALERT_RULES.sla_breach,
          modalitiesOffered: MODALITIES,
          resolvedNewInbound: resolveModalities(DEFAULT_ALERT_RULES, 'new_inbound'),
          soundEnabledByAnAgent: resolveModalities(
            { new_inbound: { scope: 'my_inbox', modalities: ['toast', 'sound'], enabled: true } },
            'new_inbound'
          ),
        }));
        """,
        tmp_path,
    )
    assert got["new_inbound"]["modalities"] == ["toast"]
    assert got["resolvedNewInbound"] == ["toast"]
    assert got["new_inbound"]["scope"] == "my_inbox"
    # The other two modalities exist for the event and can be switched on.
    assert set(got["modalitiesOffered"]) == {"sound", "desktop", "toast"}
    assert got["soundEnabledByAnAgent"] == ["toast", "sound"]
    # And the event that must stay loud is still loud.
    assert set(got["sla_breach"]["modalities"]) == {"sound", "desktop", "toast"}


def test_a_new_incoming_message_raises_the_configured_modalities(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """The happy path, run rather than described: an inbound customer message
    on an in-scope conversation resolves to exactly the modalities the rule
    configures -- no more (which would be noise) and no fewer (which would be
    silence).
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        const message = { id: 9, message_type: 0, sender_type: 'Contact', content: 'Hi' };
        const conversation = { id: 5, inbox_id: 3, meta: { assignee: { id: 7 } } };
        const viewer = { id: 7, inboxIds: [3], teamIds: [] };
        const all = { new_inbound: { scope: 'mine', modalities: ['toast','sound','desktop'], enabled: true } };
        const toastOnly = { new_inbound: { scope: 'my_inbox', modalities: ['toast'], enabled: true } };
        const off = { new_inbound: { scope: 'mine', modalities: ['toast','sound'], enabled: false } };
        const fire = rules =>
          isAlertableCustomerMessage(message) &&
          isConversationInScope(rules.new_inbound.scope, conversation, viewer)
            ? resolveModalities(rules, 'new_inbound')
            : [];
        console.log(JSON.stringify({ all: fire(all), toastOnly: fire(toastOnly), disabled: fire(off) }));
        """,
        tmp_path,
    )
    assert got["all"] == ["toast", "sound", "desktop"]
    assert got["toastOnly"] == ["toast"]
    # A disabled rule is silence for that event -- not a fallback to a default.
    assert got["disabled"] == []


def test_an_outgoing_agent_message_raises_nothing(applied: dict[str, str], tmp_path: Path) -> None:
    """The classic self-notification bug: the agent hits send, their own reply
    arrives back down the same stream, and their own machine beeps at them.

    Both guards are exercised independently, because the second one exists to
    survive the first being wrong: `message_type === 1` (outgoing) and
    `sender_type === 'User'` (a Chatwoot agent) each reject on their own, so a
    future channel delivering an agent-authored message with message_type 0
    still raises nothing.
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        const cases = {
          agentReply: { id: 1, message_type: 1, sender_type: 'User', content: 'On it' },
          agentReplyTypeOnly: { id: 2, message_type: 1, sender_type: 'Contact', content: 'On it' },
          agentReplySenderOnly: { id: 3, message_type: 0, sender_type: 'User', content: 'On it' },
          botReply: { id: 4, message_type: 1, sender_type: 'AgentBot', content: 'Auto' },
          activity: { id: 5, message_type: 2, sender_type: 'User', content: 'Assigned' },
          template: { id: 6, message_type: 3, sender_type: 'User', content: 'Template' },
          customer: { id: 7, message_type: 0, sender_type: 'Contact', content: 'Hello' },
        };
        const out = {};
        for (const [name, m] of Object.entries(cases)) out[name] = isAlertableCustomerMessage(m);
        console.log(JSON.stringify(out));
        """,
        tmp_path,
    )
    assert got["agentReply"] is False
    assert got["agentReplyTypeOnly"] is False, "the message_type guard alone must reject"
    assert got["agentReplySenderOnly"] is False, "the sender_type guard alone must reject"
    assert got["botReply"] is False
    assert got["activity"] is False
    assert got["template"] is False
    # ...and the one message that SHOULD alert still does, so the guards above
    # are not simply rejecting everything.
    assert got["customer"] is True


def test_a_private_note_raises_nothing(applied: dict[str, str], tmp_path: Path) -> None:
    """A private note is an agent talking to colleagues. It is rejected on
    `private === true` alone -- checked before the message_type test -- so it
    stays silent even for a note that somehow carries an inbound type.
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        console.log(JSON.stringify({
          note: isAlertableCustomerMessage(
            { id: 1, message_type: 1, private: true, sender_type: 'User', content: 'FYI' }
          ),
          noteWithInboundType: isAlertableCustomerMessage(
            { id: 2, message_type: 0, private: true, sender_type: 'Contact', content: 'FYI' }
          ),
          publicInbound: isAlertableCustomerMessage(
            { id: 3, message_type: 0, private: false, sender_type: 'Contact', content: 'Hi' }
          ),
        }));
        """,
        tmp_path,
    )
    assert got["note"] is False
    assert got["noteWithInboundType"] is False
    assert got["publicInbound"] is True


def test_a_conversation_outside_the_configured_scope_raises_nothing(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """Scope filtering, including the two decisions inside it that are easy to
    get backwards: an UNRECOGNISED scope alerts nothing (a typo must not
    quietly widen a rule to everything), while `my_inbox`/`my_team` FAIL OPEN
    on an unknown membership list (a failed lookup must not silence alerting --
    Chatwoot's own inbox visibility already narrowed what reaches this code).
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        const mine = { id: 5, inbox_id: 2, meta: { assignee: { id: 7 }, team: { id: 3 } } };
        const someoneElses = { id: 6, inbox_id: 9, meta: { assignee: { id: 8 }, team: { id: 4 } } };
        const unassigned = { id: 7, inbox_id: 2, meta: {} };
        const viewer = { id: 7, inboxIds: [2], teamIds: [3] };
        const blind = { id: 7, inboxIds: [], teamIds: [] };
        console.log(JSON.stringify({
          mineOwn: isConversationInScope('mine', mine, viewer),
          mineOther: isConversationInScope('mine', someoneElses, viewer),
          mineUnassigned: isConversationInScope('mine', unassigned, viewer),
          inboxOwn: isConversationInScope('my_inbox', mine, viewer),
          inboxOther: isConversationInScope('my_inbox', someoneElses, viewer),
          teamOwn: isConversationInScope('my_team', mine, viewer),
          teamOther: isConversationInScope('my_team', someoneElses, viewer),
          all: isConversationInScope('all', someoneElses, viewer),
          unknownScope: isConversationInScope('my_favourites', mine, viewer),
          inboxFailOpen: isConversationInScope('my_inbox', someoneElses, blind),
          teamFailOpen: isConversationInScope('my_team', someoneElses, blind),
        }));
        """,
        tmp_path,
    )
    assert got["mineOwn"] is True
    assert got["mineOther"] is False
    assert got["mineUnassigned"] is False
    assert got["inboxOwn"] is True
    assert got["inboxOther"] is False
    assert got["teamOwn"] is True
    assert got["teamOther"] is False
    assert got["all"] is True
    assert got["unknownScope"] is False, "an unrecognised scope must alert nothing"
    assert got["inboxFailOpen"] is True, "an unknown inbox list must not silence alerting"
    assert got["teamFailOpen"] is True


def test_denied_notification_permission_is_surfaced_with_a_re_request_affordance(
    applied: dict[str, str],
) -> None:
    """A browser that has denied notification permission drops desktop alerts
    forever and silently, and Chrome will not re-prompt after a denial. So the
    module must (a) notice the denial at the point a rule asks for 'desktop',
    (b) put a persistent affordance on screen rather than one transient toast,
    and (c) tell the agent the only thing that actually fixes it.

    Structural, not rendered: no DOM is available here (see the module
    docstring), so this reads the shipped source.
    """
    helper = applied[HELPER_REL_PATH]

    # (a) raiseAlert notices the denial on the desktop branch.
    raise_block = re.search(r"export function raiseAlert\(\{.*?\n\}", helper, re.DOTALL)
    assert raise_block, "raiseAlert not found"
    body = raise_block.group(0)
    assert "notificationPermission() === 'denied'" in body
    assert "showAlertStatus({ permissionDenied: true })" in body

    # (b) the affordance is a button on the persistent strip, wired to a
    # re-request -- not a toast that vanishes in six seconds.
    status_block = re.search(r"export function showAlertStatus\(\{.*?\n\}", helper, re.DOTALL)
    assert status_block, "showAlertStatus not found"
    strip = status_block.group(0)
    assert "'Enable notifications'" in strip
    assert "addEventListener('click', () => requestNotificationPermission())" in strip
    assert "Desktop notifications are blocked" in strip

    # (c) re-requesting from 'denied' cannot succeed, so the message has to name
    # the browser settings rather than pretending the button is enough.
    request_block = re.search(
        r"export function requestNotificationPermission\(\).*?\n\}", helper, re.DOTALL
    )
    assert request_block
    assert "browser settings" in request_block.group(0)
    # A granted answer clears the strip rather than leaving a stale warning up.
    assert "hideAlertStatus();" in request_block.group(0)


def test_the_my_tasks_app_behaviour_is_unchanged() -> None:
    """This package is an ADDITION. The my-tasks app keeps its own SLA alerts;
    trading a working alert surface for a new one would be a net loss.

    Two directions, so a change hiding in either is caught: the patch touches
    only the six files it declares (none of them the my-tasks app), and the
    my-tasks app still contains its own copies of all four primitives plus its
    own 60-second poll and its own SLA warn/breach thresholds.
    """
    touched = sorted(set(re.findall(r"^\+\+\+ b/(.+)$", DIFF_TEXT, re.M)))
    assert touched == sorted(
        [
            API_REL_PATH,
            COMPOSABLE_REL_PATH,
            HELPER_REL_PATH,
            PAGE_REL_PATH,
            ROUTES_REL_PATH,
            SIDEBAR_REL_PATH,
        ]
    ), touched
    assert not any("my-tasks" in path for path in touched)

    my_tasks = _MY_TASKS_PATH.read_text(encoding="utf-8")
    for verbatim in (
        "function beep(freq, durationMs) {",
        "function requestNotificationPermission() {",
        "function sendDesktopNotification(title, body) {",
        "function toast(kind, msg) {",
        "const POLL_INTERVAL_MS = 60_000;",
        "function checkNotifications(tasks) {",
        "if (t.breachType === 'UNRESOLVED' || t.breachType === 'NO_RESPONSE') {",
    ):
        assert verbatim in my_tasks, f"my-tasks lost its own primitive: {verbatim!r}"

    # The fork module is a transcription, not a shared import: the my-tasks app
    # is a standalone iframe page with no bundler, so it cannot import from the
    # Chatwoot bundle even if we wanted it to.
    assert "dashboard/helper/protonAlerts" not in my_tasks


# ---------------------------------------------------------------------------
# Task 3 -- stream subscription with poll fallback
# ---------------------------------------------------------------------------


def test_alerts_are_raised_from_the_stream_when_it_is_connected(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """The primary path is the store the ActionCable connector writes into --
    not a poll. A 60-second poll as the PRIMARY source would notify an agent up
    to a minute after the customer's message is already visible in their list,
    which is worse than no alert because it trains agents to ignore it.

    So: a readable conversation list means stream coverage, stream coverage
    means stream mode, and stream mode means no degraded indicator.
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        const conv = { id: 5, inbox_id: 2, messages: [
          { id: 11, message_type: 0, sender_type: 'Contact', content: 'Hi' },
        ] };
        const store = { getters: { getAllConversations: [conv] } };
        const read = readConversations(store);
        const mode = nextAlertMode('stream', {
          streamSourceAvailable: read.coverage === 'list',
          streamDelivered: true,
          staleFoundByPoll: false,
        });
        console.log(JSON.stringify({
          coverage: read.coverage,
          count: read.conversations.length,
          mode,
          reason: degradedReason(mode, true),
          signatureChanges: [
            conversationSignature([conv]),
            conversationSignature([{ ...conv, messages: [...conv.messages,
              { id: 12, message_type: 0, sender_type: 'Contact', content: 'again' }] }]),
          ],
          watchdogMs: STREAM_WATCHDOG_MS,
        }));
        """,
        tmp_path,
    )
    assert got["coverage"] == "list"
    assert got["count"] == 1
    assert got["mode"] == "stream"
    assert got["reason"] is None
    # The watcher fires because the signature actually changes on a new message.
    assert got["signatureChanges"][0] != got["signatureChanges"][1]
    assert got["watchdogMs"] == 60000

    # The watcher is wired to that signature, and the composable's primary
    # source is the store rather than the poll.
    composable = applied[COMPOSABLE_REL_PATH]
    assert "conversationSignature(readConversations(store).conversations)" in composable
    assert re.search(r"watch\(\s*\(\) => conversationSignature", composable)


def test_a_stream_disconnect_activates_the_sixty_second_poll(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """Two ways the stream can be absent, and both must land on the poll:

    1. No readable conversation list at all (the getter guess was wrong, or the
       store has not populated) -- straight to poll mode.
    2. A readable list that is not DELIVERING: the poll finds an inbound
       message older than the 90 s grace window which the stream never
       delivered. That is proof, not inference -- and a quiet period, which
       produces no evidence either way, must NOT be mistaken for a
       disconnection, or every quiet morning shows a degraded strip.
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        console.log(JSON.stringify({
          noSource: nextAlertMode('stream', { streamSourceAvailable: false }),
          staleFound: nextAlertMode('stream', {
            streamSourceAvailable: true, staleFoundByPoll: true }),
          quietPeriod: nextAlertMode('stream', {
            streamSourceAvailable: true, staleFoundByPoll: false, streamDelivered: false }),
          selectedOnlyCoverage: readConversations(
            { getters: { getSelectedChat: { id: 1, messages: [] } } }).coverage,
          noCoverage: readConversations({ getters: {} }).coverage,
          graceMs: STREAM_GRACE_MS,
          watchdogMs: STREAM_WATCHDOG_MS,
        }));
        """,
        tmp_path,
    )
    assert got["noSource"] == "poll"
    assert got["staleFound"] == "poll"
    assert got["quietPeriod"] == "stream", "silence is not evidence of a disconnection"
    # Only the open conversation is not the surface an agent needs alerting on,
    # so it counts as degraded coverage, not healthy.
    assert got["selectedOnlyCoverage"] == "selected"
    assert got["noCoverage"] == "none"
    assert got["graceMs"] == 90000
    assert got["watchdogMs"] == 60000

    composable = applied[COMPOSABLE_REL_PATH]
    # The poll runs on the watchdog interval and hits Chatwoot's own API --
    # same-origin, no new backend endpoint, works when the socket does not.
    assert "setInterval(runWatchdog, STREAM_WATCHDOG_MS)" in composable
    assert "/api/v1/accounts/${accountId}/conversations?status=open" in composable
    assert "now - createdAt > STREAM_GRACE_MS" in composable


def test_the_degraded_indicator_is_shown_while_polling(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """Silent failure in an alerting system is the worst outcome available, so
    poll mode is never invisible. The two reasons say different things, because
    "interrupted" and "unavailable" need different operator responses.
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        console.log(JSON.stringify({
          stale: degradedReason('poll', true),
          absent: degradedReason('poll', false),
          healthy: degradedReason('stream', true),
          text: DEGRADED_TEXT,
        }));
        """,
        tmp_path,
    )
    assert got["stale"] == "stream-stale"
    assert got["absent"] == "no-stream-source"
    assert got["healthy"] is None
    assert "60s" in got["text"]["stream-stale"]
    assert "60s" in got["text"]["no-stream-source"]
    assert got["text"]["stream-stale"] != got["text"]["no-stream-source"]

    # It is painted after every mode decision on both paths, not just once at
    # boot -- a strip that only appears on load would miss the disconnection it
    # exists to report.
    composable = applied[COMPOSABLE_REL_PATH]
    stream_scan = re.search(r"const scanStream = \(\) => \{(.*?)\n  \};", composable, re.DOTALL)
    watchdog = re.search(r"const runWatchdog = async \(\) => \{(.*?)\n  \};", composable, re.DOTALL)
    assert stream_scan and watchdog
    assert "paintIndicator();" in stream_scan.group(1)
    # Twice in the watchdog: once on the branch where the poll itself failed
    # (where the stream may also be gone) and once after a successful sweep.
    assert watchdog.group(1).count("paintIndicator();") == 2
    paint = re.search(r"const paintIndicator = \(\) => \{(.*?)\n  \};", composable, re.DOTALL)
    assert paint, "paintIndicator not found"
    assert "showAlertStatus({ reason })" in paint.group(1)
    assert "hideAlertStatus()" in paint.group(1)


def test_reconnecting_returns_to_stream_mode_and_hides_the_indicator(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """Recovery has to be evidence-based too, and the evidence is the stream
    actually delivering something. Note the asymmetry: coverage returning is
    not enough on its own to claim health while the poll is still finding stale
    messages -- otherwise the strip would flap.
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        const reconnected = nextAlertMode('poll', {
          streamSourceAvailable: true, streamDelivered: true, staleFoundByPoll: false });
        console.log(JSON.stringify({
          reconnected,
          reasonAfter: degradedReason(reconnected, true),
          stillStale: nextAlertMode('poll', {
            streamSourceAvailable: true, streamDelivered: true, staleFoundByPoll: true }),
          coverageAloneIsNotRecovery: nextAlertMode('poll', {
            streamSourceAvailable: true, streamDelivered: false, staleFoundByPoll: false }),
        }));
        """,
        tmp_path,
    )
    assert got["reconnected"] == "stream"
    assert got["reasonAfter"] is None, "the indicator must be hidden again on recovery"
    assert got["stillStale"] == "poll"
    assert got["coverageAloneIsNotRecovery"] == "poll"

    # The watchdog re-probes coverage each tick, so a store that populates late
    # can end degraded mode without a page reload.
    composable = applied[COMPOSABLE_REL_PATH]
    assert "streamCoverage.value = readConversations(store).coverage;" in composable


def test_no_alert_is_raised_twice_when_both_paths_briefly_overlap(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """THE RECONNECTION RACE. The poll fires, the stream reconnects, and both
    see the same message. A duplicate-alert storm on reconnect teaches agents
    to ignore alerts exactly as effectively as a beep every few seconds does,
    so this is not an optimisation.

    Deduplication is on MESSAGE id through one Set shared by both paths -- so a
    second message on the same conversation is still news, and the set is
    bounded so a long shift cannot grow it without limit.
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        const seen = new Set();
        const k = alertKey('new_inbound', 5, 11);
        const pollFirst = claimAlert(seen, k);      // the poll gets there first
        const streamSecond = claimAlert(seen, k);   // ...then the stream reconnects
        const otherMessage = claimAlert(seen, alertKey('new_inbound', 5, 12));
        const otherEvent = claimAlert(seen, alertKey('sla_breach', 5, 11));
        // The bound: pushing past the limit evicts the oldest key, and the
        // most recent claim is still remembered.
        const bounded = new Set();
        for (let i = 0; i < MAX_REMEMBERED_ALERT_KEYS + 50; i += 1) {
          claimAlert(bounded, alertKey('new_inbound', 1, i));
        }
        console.log(JSON.stringify({
          pollFirst, streamSecond, otherMessage, otherEvent,
          keyShape: k,
          boundedSize: bounded.size,
          limit: MAX_REMEMBERED_ALERT_KEYS,
          lastStillRemembered: !claimAlert(
            bounded, alertKey('new_inbound', 1, MAX_REMEMBERED_ALERT_KEYS + 49)),
        }));
        """,
        tmp_path,
    )
    assert got["pollFirst"] is True
    assert got["streamSecond"] is False, "the second path must raise nothing"
    assert got["otherMessage"] is True, "a different message is still news"
    assert got["otherEvent"] is True
    assert got["keyShape"] == "new_inbound:5:11"
    assert got["boundedSize"] <= got["limit"]
    assert got["lastStillRemembered"] is True

    # And both paths really do share one set, rather than each keeping its own.
    composable = applied[COMPOSABLE_REL_PATH]
    assert composable.count("const seen = new Set();") == 1
    assert "if (!claimAlert(seen, key)) return false;" in composable
    assert "if (seen.has(key)) continue;" in composable


def test_a_rule_store_outage_leaves_alerting_on_the_built_in_defaults(
    applied: dict[str, str], tmp_path: Path
) -> None:
    """The other way an alerting system goes silent: its configuration lookup
    fails. `{disabled: true}` (ALERT_RULES_ENABLED off), a network failure and a
    malformed body must all leave the built-in defaults in force -- the same
    fail-open position rules_store.py takes for a store outage.
    """
    got = _node_eval(
        applied[HELPER_REL_PATH],
        """
        const events = ALERT_EVENTS;
        const shape = merged => events.map(e => [e, merged[e].modalities.join('+')]);
        console.log(JSON.stringify({
          nullPayload: shape(mergeAlertRules(null)),
          disabled: shape(mergeAlertRules({ disabled: true, rules: {} })),
          garbage: shape(mergeAlertRules({ rules: 'nonsense' })),
          partial: shape(mergeAlertRules({ rules: { new_inbound: { scope: 'all', modalities: ['sound'], enabled: true } } })),
          badScopeIgnored: mergeAlertRules({ rules: { new_inbound: { scope: 'wat', modalities: ['toast'] } } }).new_inbound.scope,
          defaults: shape(mergeAlertRules({ rules: {} })),
        }));
        """,
        tmp_path,
    )
    expected = [
        ["assigned_to_me", "sound+desktop+toast"],
        ["new_inbound", "toast"],
        ["sla_warn", "toast"],
        ["sla_breach", "sound+desktop+toast"],
        ["escalated", "toast"],
        ["anomaly", "desktop"],
    ]
    assert got["nullPayload"] == expected
    assert got["disabled"] == expected
    assert got["garbage"] == expected
    assert got["defaults"] == expected
    # A real answer still wins where it is present...
    assert dict(got["partial"])["new_inbound"] == "sound"
    # ...but only where it is well-formed.
    assert got["badScopeIgnored"] == "my_inbox"


# ---------------------------------------------------------------------------
# Task 6 -- the alert-preferences UI
#
# The brief's five task-6 tests exercise features/alerts/rules_router.py, which
# a sibling task owns and covers. These are the FORK side of the same task,
# named for what they check rather than reusing those names.
# ---------------------------------------------------------------------------


def test_the_preferences_page_writes_only_the_agents_own_overrides(
    applied: dict[str, str],
) -> None:
    """An agent's Save must hit `/alerts/rules/mine/:event`, never
    `/defaults/:event`. This is the difference between one agent turning their
    own sound off and one agent turning the whole team's sla_breach default
    down, which would go unnoticed until the team missed one.
    """
    page = applied[PAGE_REL_PATH]
    save_mine = re.search(r"async saveMine\(event\) \{(.*?)\n    \},", page, re.DOTALL)
    assert save_mine, "saveMine not found"
    assert "putMyAlertRule(event," in save_mine.group(1)
    assert "putAlertDefault" not in save_mine.group(1)

    save_default = re.search(r"async saveDefault\(event\) \{(.*?)\n    \},", page, re.DOTALL)
    assert save_default, "saveDefault not found"
    assert "putAlertDefault(event," in save_default.group(1)
    assert "putMyAlertRule" not in save_default.group(1)

    # The agent's own rows are always editable; only the defaults block is
    # permission-gated (below). And the agent id is passed so the RBAC-off mode
    # is not a 400.
    assert "putMyAlertRule(event, this.body(this.mine[event]), this.agentId)" in page


def test_the_account_defaults_section_is_gated_on_alerts_manage(
    applied: dict[str, str],
) -> None:
    """`alerts.manage` gates CHANGING a default, not SEEING one -- knowing what
    you inherit is not privileged information, and hiding it would leave an
    agent unable to tell an override from an inheritance.

    `canManageDefaults` must be a computed, not a data field: the permission
    set loads asynchronously, so a value snapshotted in data() would be false
    on first render and never correct itself -- the bug ProtonMyStatusPage.vue
    documents for its own canManage.
    """
    page = applied[PAGE_REL_PATH]
    assert re.search(
        r"computed: \{.*?canManageDefaults\(\) \{\s*return protonHasPermission\('alerts\.manage'\);",
        page,
        re.DOTALL,
    )
    # The read is not gated...
    assert "getAlertDefaults()" in page
    # ...the writes are, in the markup and on every input.
    assert 'v-if="canManageDefaults"' in page
    assert ':disabled="!canManageDefaults"' in page
    # And it tells a non-admin why the section is read-only rather than just
    # greying it out.
    assert "alerts.manage" in page and "Read-only" in page

    # The nav entry uses the agent-level permission, so the page an agent needs
    # is not hidden behind the admin one.
    assert "protonHasPermission('alerts.set_own_preferences')" in applied[SIDEBAR_REL_PATH]


def test_resetting_an_override_returns_the_row_to_the_account_default(
    applied: dict[str, str],
) -> None:
    """Reset calls DELETE and repaints from the rule the router returns after
    the reset, rather than guessing the inherited value client-side -- the
    router answers with the resolved rule precisely so this page does not have
    to reimplement resolution and drift from it.
    """
    page = applied[PAGE_REL_PATH]
    reset = re.search(r"async resetMine\(event\) \{(.*?)\n    \},", page, re.DOTALL)
    assert reset, "resetMine not found"
    body = reset.group(1)
    assert "resetMyAlertRule(event, this.agentId)" in body
    assert "result.rule" in body
    assert "this.mine[event] = {" in body

    api = applied[API_REL_PATH]
    reset_api = re.search(r"export function resetMyAlertRule\(.*?\n\}", api, re.DOTALL)
    assert reset_api
    assert "method: 'DELETE'" in reset_api.group(0)


def test_the_page_reports_the_disabled_flag_rather_than_showing_an_empty_table(
    applied: dict[str, str],
) -> None:
    """With ALERT_RULES_ENABLED off, rules_router.py answers
    `{"disabled": true, "reason": ...}` on every endpoint. An empty page would
    read as "you have no alerts", which is the opposite of the truth: everyone
    gets the built-in defaults. So the page shows the reason AND still renders
    six rows populated from those defaults.
    """
    page = applied[PAGE_REL_PATH]
    assert "if (mine.disabled)" in page
    assert "this.disabledReason = mine.reason" in page
    assert 'v-if="disabled"' in page
    assert "built-in defaults" in page

    hydrate = re.search(r"hydrate\(rules\) \{(.*?)\n    \},", page, re.DOTALL)
    assert hydrate, "hydrate not found"
    assert "DEFAULT_ALERT_RULES[event]" in hydrate.group(1)
    # ...and the defaults it falls back to are the module's, not a second copy
    # that could drift from what actually fires.
    assert "from 'dashboard/helper/protonAlerts'" in page


def test_the_patch_declares_its_stacking_and_its_unverified_claims() -> None:
    """A line-number fix-up to a lower patch cascades, so stacking has to be
    declared rather than discovered. And the two switches are not the same
    switch: `INBOUND_ALERTS_ENABLED` (backend Settings) does not populate
    `PROTON_FEATURES`, which is what `hasFeature('inbound_alerts')` reads. A
    header that implied otherwise would send an operator to flip a flag that
    changes nothing.
    """
    header = PATCH_TEXT[: PATCH_TEXT.index("diff --git")]
    assert "stacks on 0025" in header
    assert "0043" in header and "0003" in header
    assert "0053" in header and "0054" in header
    # The honest statements, each of which someone downstream needs.
    assert "NOT verified with `git apply --check` against a real" in header
    assert "TWO INDEPENDENT SWITCHES" in header
    assert "INBOUND_ALERTS_ENABLED" in header and "PROTON_FEATURES" in header
    assert "amd64" in header and "never on the prod VM" in header
    # The register row must exist, or the owed verifications are only in a
    # commit message nobody re-reads.
    register = (_REPO_ROOT / "docs" / "analysis" / "2026-08-09-blocked-work-register.md").read_text(
        encoding="utf-8"
    )
    assert "0057" in register
    assert "inbound-alerts" in register or "0057-inbound-alerts" in register
