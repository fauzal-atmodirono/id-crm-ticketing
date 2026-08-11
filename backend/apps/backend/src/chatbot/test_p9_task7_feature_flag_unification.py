"""P9 task 7 -- one switch per feature, and it is the documented one.

`INBOUND_ALERTS_ENABLED` did not gate the alert module and
`FAQ_SUGGESTION_POPUP_ENABLED` did not gate the FAQ strip. Both are gated
client-side on `hasFeature('<name>')`, which reads a tenant's `PROTON_FEATURES`
list, and nothing connected the two -- so an operator flipped the documented
switch, saw nothing, and could not tell a mis-set tenant from a broken feature.
It happened twice (blocked-work register 3g and 3h), which is why the fix here is
mechanical rather than a third paragraph of documentation.

The fix is two files:

* `deploy/docker-compose.tenant.yml` forwards both flags from
  `tenants/<tenant>.env` into `x-chatwoot-env`, the anchor `chatwoot-rails` and
  `chatwoot-sidekiq` share. The `backend` service already reads the same file
  wholesale via `env_file:`, so one variable now reaches both halves.
* `deploy/chatwoot-fork/patches/0058-feature-flag-unification.patch` makes the
  Rails layout build `features` as `PROTON_FEATURES` **plus** the feature name of
  every mapped flag that is truthy in the environment.

**What these tests do and do not prove.** They apply 0001 then 0058 with a real
`git apply` to a synthetic pre-image, and -- where Ruby is available -- they
render the SHIPPED ERB fragment through Ruby's own `erb` library and read the
`features` list out of the emitted HTML. That is real output from the real
template text, not a Python re-implementation of it (the same discipline 0057's
tests apply to JavaScript in node). They prove nothing about a browser: no
`window.__PROTON_CONFIG__` has ever been observed in one, and neither patch has
been through a Cloud Build. See the blocked-work register, sections 3g and 3h.

The mapping's feature NAMES are not hardcoded here. They are read back out of
0056's and 0057's own `hasFeature('...')` calls, because a typo in the map would
produce exactly the failure this task exists to remove -- a flag that looks wired
and switches nothing -- and a hardcoded expectation could not tell the two apart.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
PATCH_DIR = _REPO_ROOT / "deploy" / "chatwoot-fork" / "patches"
PATCH_0001 = PATCH_DIR / "0001-runtime-config.patch"
PATCH_0058 = PATCH_DIR / "0058-feature-flag-unification.patch"
PATCH_0056 = PATCH_DIR / "0056-faq-composer-apply.patch"
PATCH_0057 = PATCH_DIR / "0057-inbound-alerts.patch"
COMPOSE_PATH = _REPO_ROOT / "deploy" / "docker-compose.tenant.yml"
DEFAULT_ENV_PATH = _REPO_ROOT / "deploy" / "tenants" / "default.env"

for _p in (PATCH_0001, PATCH_0058, PATCH_0056, PATCH_0057, COMPOSE_PATH, DEFAULT_ENV_PATH):
    assert _p.is_file(), f"not found: {_p}"

LAYOUT_REL_PATH = "app/views/layouts/vueapp.html.erb"

PATCH_0058_TEXT = PATCH_0058.read_text(encoding="utf-8")
COMPOSE_TEXT = COMPOSE_PATH.read_text(encoding="utf-8")

# The flags this task unifies, and the file each is documented in. The FEATURE
# NAME each maps to is deliberately absent -- see the module docstring.
_UNIFIED_FLAGS = ("INBOUND_ALERTS_ENABLED", "FAQ_SUGGESTION_POPUP_ENABLED")

_RUBY = shutil.which("ruby")


# ---------------------------------------------------------------------------
# Applying 0001 then 0058 to a synthetic pre-image
# ---------------------------------------------------------------------------

# Upstream's own lines around the block 0001 inserts, transcribed verbatim from
# 0001's merged diff -- the three lines of leading context and the three of
# trailing context it declares. Nothing else about `vueapp.html.erb` is known
# here, and nothing else needs to be: 0001 is the ONLY patch in the series that
# touches this file, so its post-image is 0058's exact pre-image.
_LAYOUT_LEADING_CONTEXT = (
    "      }",
    "      window.errorLoggingConfig = "
    "'<%= ENV.fetch('SENTRY_FRONTEND_DSN', '') || ENV.fetch('SENTRY_DSN', '') %>'",
    "    </script>",
)
_LAYOUT_TRAILING_CONTEXT = (
    "    <% if @global_config['CLOUD_ANALYTICS_TOKEN'].present? %>",
    "    <script>",
    "      window.analyticsConfig = {",
)
# 0001's hunk header is `@@ -64,6 +64,13 @@`, so its first context line is
# upstream line 64 and 63 lines precede it.
_LAYOUT_LEADING_PAD = 63


def _synthetic_upstream_layout() -> str:
    lines = [f"    <!-- upstream {LAYOUT_REL_PATH} line {i} -->" for i in range(1, 64)]
    lines.extend(_LAYOUT_LEADING_CONTEXT)
    lines.extend(_LAYOUT_TRAILING_CONTEXT)
    return "\n".join(lines) + "\n"


def _run(tree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Every argument is a hardcoded literal (git plumbing / ruby) -- never
    # untrusted input -- so the subprocess call is safe despite S603.
    return subprocess.run(args, cwd=tree, capture_output=True, text=True, check=False)  # noqa: S603


@pytest.fixture(scope="module")
def applied_layout(tmp_path_factory: pytest.TempPathFactory) -> str:
    """`vueapp.html.erb` after a real `git apply` of 0001 and then 0058.

    Applying **0001 as well**, rather than hand-writing its post-image, is what
    makes the stacking claim a check rather than an assertion: if 0058's context
    ever drifts from what 0001 actually produces, this fixture fails.
    """
    tree = tmp_path_factory.mktemp("patch0058-tree")
    path = tree / LAYOUT_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_synthetic_upstream_layout(), encoding="utf-8")

    assert _run(tree, "git", "init", "-q").returncode == 0
    assert _run(tree, "git", "add", "-A").returncode == 0
    assert (
        _run(
            tree, "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base"
        ).returncode
        == 0
    )

    # 0001 also touches useProtonConfig.js, which does not exist in this tree;
    # --include restricts the apply to the layout, which is the only file 0058
    # stacks on.
    first = _run(tree, "git", "apply", "--include", LAYOUT_REL_PATH, str(PATCH_0001))
    assert first.returncode == 0, f"0001 did not apply to the synthetic upstream: {first.stderr}"

    check = _run(tree, "git", "apply", "--check", str(PATCH_0058))
    assert check.returncode == 0, (
        "0058 did not apply on top of 0001's own post-image "
        f"(internal consistency only, not a real fork): {check.stderr}"
    )
    assert _run(tree, "git", "apply", str(PATCH_0058)).returncode == 0

    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Rendering the shipped ERB
# ---------------------------------------------------------------------------


def _erb_fragment(layout: str) -> str:
    """The shipped `<% ... %>` block plus the `<script>` block it feeds.

    Sliced out of the APPLIED file rather than re-typed, so what runs below is
    the exact text the patch ships. The surrounding upstream lines are left
    behind because they reference `@global_config`, which only exists inside a
    real Rails render.
    """
    start = layout.index("    <%\n")
    end = layout.index("    </script>", layout.index("window.__PROTON_CONFIG__"))
    return layout[start : end + len("    </script>")]


def _render_features(fragment: str, env: dict[str, str], tmp_path: Path) -> list[str]:
    """Render `fragment` in Ruby with `env` as the environment, and return the
    `features` list the emitted JavaScript would build.

    Skips (never silently passes) where Ruby is unavailable, so a green run
    always means the shipped ERB actually executed.
    """
    if _RUBY is None:  # pragma: no cover - environment dependent
        pytest.skip("ruby is not available; the shipped ERB cannot be executed here")
    harness = tmp_path / "render.rb"
    harness.write_text(
        "require 'erb'\nrequire 'json'\n"
        "%w[PROTON_FEATURES INBOUND_ALERTS_ENABLED FAQ_SUGGESTION_POPUP_ENABLED]"
        ".each { |k| ENV.delete(k) }\n"
        "JSON.parse(File.read(ARGV[1])).each { |k, v| ENV[k] = v }\n"
        "out = ERB.new(File.read(ARGV[0])).result(binding)\n"
        'print JSON.generate(out.match(/features: "([^"]*)"/)[1]'
        ".split(',').reject(&:empty?))\n",
        encoding="utf-8",
    )
    frag_file = tmp_path / "fragment.erb"
    frag_file.write_text(fragment, encoding="utf-8")
    env_file = tmp_path / "env.json"
    env_file.write_text(json.dumps(env), encoding="utf-8")
    result = subprocess.run(  # noqa: S603
        [_RUBY, str(harness), str(frag_file), str(env_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"ruby failed to render the shipped ERB: {result.stderr}"
    return list(json.loads(result.stdout))


# ---------------------------------------------------------------------------
# The feature names the fork actually reads
# ---------------------------------------------------------------------------


def _fork_feature_names() -> set[str]:
    """Every `hasFeature('<name>')` the fork patches read, harvested from them.

    The point of harvesting rather than listing: a name in the ERB map that no
    `hasFeature` call reads is a flag that looks wired and gates nothing, which
    is the exact defect this task closes.
    """
    names: set[str] = set()
    for patch in sorted(PATCH_DIR.glob("*.patch")):
        for match in re.finditer(
            r"hasFeature\(\s*'([a-z0-9_]+)'\s*\)", patch.read_text(encoding="utf-8")
        ):
            names.add(match.group(1))
    return names


def _erb_flag_map(layout: str) -> dict[str, str]:
    """The `ENV_NAME => feature_name` pairs out of the applied ERB block."""
    block = layout[layout.index("proton_flag_features = {") :]
    block = block[: block.index("}")]
    return dict(re.findall(r"'([A-Z0-9_]+)'\s*=>\s*'([a-z0-9_]+)'", block))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_the_patch_applies_onto_0001s_own_post_image(applied_layout: str) -> None:
    """Named for what it checks. There is no
    `..._onto_the_pinned_upstream_ref` test here and there cannot honestly be
    one: this sandbox has no network to clone upstream, so what is verified is
    internal consistency with 0001's declared context, not the real fork."""
    assert "proton_flag_features = {" in applied_layout
    assert "features: \"<%= proton_features.join(',') %>\"" in applied_layout
    # The line it replaces is gone, so the old single-source read cannot survive
    # alongside the new one.
    assert "ENV.fetch('PROTON_FEATURES', '') %>\".split" not in applied_layout
    # And 0001's other two values are untouched.
    assert "backendUrl: \"<%= ENV.fetch('PROTON_BACKEND_URL', '') %>\"" in applied_layout
    assert "backendKey: \"<%= ENV.fetch('PROTON_BACKEND_KEY', '') %>\"" in applied_layout


def test_every_mapped_flag_names_a_feature_the_fork_actually_gates_on(
    applied_layout: str,
) -> None:
    """The reachability assertion, and the one that would have caught this class
    of bug in the first place: each mapped feature name must be a name some
    `hasFeature(...)` call in the patch series really reads."""
    mapping = _erb_flag_map(applied_layout)
    assert set(mapping) == set(_UNIFIED_FLAGS), mapping

    read_by_fork = _fork_feature_names()
    assert "inbound_alerts" in read_by_fork, "0057's gate name changed"
    assert "faq_suggestion_popup" in read_by_fork, "0056's gate name changed"
    unread = sorted(name for name in mapping.values() if name not in read_by_fork)
    assert not unread, (
        f"mapped feature names no hasFeature() call reads (a flag that gates nothing): {unread}"
    )


def test_the_compose_file_gives_rails_the_same_variable_the_backend_reads() -> None:
    """One switch means one variable, reaching both services.

    The `backend` service takes `tenants/<tenant>.env` wholesale via `env_file:`,
    so it already sees these. The Chatwoot services have no `env_file:` at all --
    they get only the `x-chatwoot-env` anchor -- so the passthrough has to be
    there explicitly or the Rails layout has nothing to read.
    """
    anchor = COMPOSE_TEXT.split("x-chatwoot-env: &chatwoot-env", 1)[1].split("\nnetworks:", 1)[0]
    for flag in _UNIFIED_FLAGS:
        assert f"{flag}: ${{{flag}:-false}}" in anchor, (
            f"{flag} is not forwarded to the Chatwoot containers; the fork's "
            "hasFeature() gate would never see it"
        )
    # Both Chatwoot services must actually use the anchor, or forwarding into it
    # achieves nothing for whichever one does not.
    assert COMPOSE_TEXT.count("<<: *chatwoot-env") >= 2
    # The backend's own path is unchanged and still the tenant env file.
    assert "env_file:\n      - tenants/${TENANT}.env" in COMPOSE_TEXT


def test_the_default_is_still_off_for_a_tenant_that_sets_neither(
    applied_layout: str, tmp_path: Path
) -> None:
    """The ship-dark guarantee, rendered rather than reasoned about.

    `deploy/tenants/default.env` sets neither flag, so it must render the exact
    list it renders today -- and the compose default (`:-false`) must contribute
    nothing even when the variable IS present and false, which is the state every
    existing tenant will be in the moment this compose file lands.
    """
    fragment = _erb_fragment(applied_layout)
    for flag in _UNIFIED_FLAGS:
        assert f"{flag}=" not in DEFAULT_ENV_PATH.read_text(encoding="utf-8")

    listed = "ai_assist,nav_menu,copilot,knowledge"
    assert _render_features(fragment, {"PROTON_FEATURES": listed}, tmp_path) == listed.split(",")
    assert _render_features(
        fragment,
        {
            "PROTON_FEATURES": listed,
            "INBOUND_ALERTS_ENABLED": "false",
            "FAQ_SUGGESTION_POPUP_ENABLED": "false",
        },
        tmp_path,
    ) == listed.split(",")
    # And with nothing set at all, an empty list -- not a list containing "".
    assert _render_features(fragment, {}, tmp_path) == []


def test_turning_the_documented_flag_on_is_what_switches_the_feature_on(
    applied_layout: str, tmp_path: Path
) -> None:
    """The deliverable. One variable, and the SPA's own gate sees it.

    Also covers the truthy set: it matches pydantic-settings' bool parsing, so a
    tenant writing `y` or `1` or `TRUE` cannot end up with the backend on and the
    frontend off -- a third variant of the same two-switch state.
    """
    fragment = _erb_fragment(applied_layout)

    got = _render_features(fragment, {"INBOUND_ALERTS_ENABLED": "true"}, tmp_path)
    assert got == ["inbound_alerts"]
    got = _render_features(fragment, {"FAQ_SUGGESTION_POPUP_ENABLED": "true"}, tmp_path)
    assert got == ["faq_suggestion_popup"]

    for truthy in ("true", "TRUE", "1", "y", "yes", "on", "  true  "):
        assert "inbound_alerts" in _render_features(
            fragment, {"INBOUND_ALERTS_ENABLED": truthy}, tmp_path
        ), truthy
    for falsy in ("false", "0", "no", "off", "", "  "):
        assert "inbound_alerts" not in _render_features(
            fragment, {"INBOUND_ALERTS_ENABLED": falsy}, tmp_path
        ), falsy


def test_proton_features_still_works_and_is_never_overridden(
    applied_layout: str, tmp_path: Path
) -> None:
    """Additive, never subtractive.

    Two properties that together make this safe to land on a live tenant: a name
    already in `PROTON_FEATURES` survives whatever the flag says (so nothing that
    works today stops working), and a flag-derived name is not duplicated when
    the operator has also listed it by hand.
    """
    fragment = _erb_fragment(applied_layout)

    # Listed by hand, flag off: kept. This is the case that must not regress --
    # it is how any tenant that followed register 3g/3h's advice is configured.
    assert _render_features(
        fragment,
        {"PROTON_FEATURES": "ai_assist,inbound_alerts", "INBOUND_ALERTS_ENABLED": "false"},
        tmp_path,
    ) == ["ai_assist", "inbound_alerts"]

    # Listed by hand AND flag on: once, not twice.
    assert _render_features(
        fragment,
        {"PROTON_FEATURES": "inbound_alerts", "INBOUND_ALERTS_ENABLED": "true"},
        tmp_path,
    ) == ["inbound_alerts"]

    # Whitespace in the list is tolerated exactly as `.filter(Boolean)` did.
    assert _render_features(fragment, {"PROTON_FEATURES": " ai_assist , nav_menu ,"}, tmp_path) == [
        "ai_assist",
        "nav_menu",
    ]


def test_no_javascript_changes_so_0056_and_0057_are_unaffected() -> None:
    """The fix is one Ruby hash and two compose lines. The gates themselves are
    untouched, which is why this cannot break either patch's own tests."""
    assert PATCH_0058_TEXT.count("diff --git") == 1
    assert f"diff --git a/{LAYOUT_REL_PATH} b/{LAYOUT_REL_PATH}" in PATCH_0058_TEXT
    assert "app/javascript" not in PATCH_0058_TEXT
    # And the two gates it targets are still where they were.
    assert "hasFeature('inbound_alerts')" in PATCH_0057.read_text(encoding="utf-8")
    assert "hasFeature('faq_suggestion_popup')" in PATCH_0056.read_text(encoding="utf-8")


def test_the_patch_declares_its_stacking_and_its_unverified_claims() -> None:
    """A patch whose header overstates its evidence is how an unverified change
    gets cited as verified in a client review."""
    # Whitespace-normalised: the header is hard-wrapped, so every phrase below
    # spans a line break in the file.
    header = " ".join(PATCH_0058_TEXT.split("diff --git", 1)[0].split())
    assert "0001" in header, "the header must declare what it stacks on"
    assert "Cloud Build" in header
    assert "has never been applied to a real Chatwoot checkout" in header
    assert "git diff" in header, "the header must say the hunk arithmetic is git's"
    # It must not claim a verification that has not happened.
    lowered = header.lower()
    assert "verified in a browser" not in lowered
    assert "screenshot" not in lowered
