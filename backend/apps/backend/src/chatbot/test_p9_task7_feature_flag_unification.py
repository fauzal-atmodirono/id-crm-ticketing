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
`features` list out of the emitted HTML. Where the `docker compose` CLI is
available they also render the REAL `deploy/docker-compose.tenant.yml` against a
synthetic one-line tenant env file and read the resolved environment of each
service back out, then feed the value Compose resolved for `chatwoot-rails`
straight into the ERB. So the full chain -- one line in `tenants/<tenant>.env`,
through Compose's interpolation, into Rails' environment, through the shipped
ERB, out as the `features` list the SPA's `hasFeature()` reads -- is executed by
the real tools at every step, not re-implemented in Python. They prove nothing
about a browser: no `window.__PROTON_CONFIG__` has ever been observed in one, and
neither patch has been through a Cloud Build. See the blocked-work register,
sections 3g and 3h.

The mapping's feature NAMES are not hardcoded here. They are read back out of
0056's and 0057's own `hasFeature('...')` calls, because a typo in the map would
produce exactly the failure this task exists to remove -- a flag that looks wired
and switches nothing -- and a hardcoded expectation could not tell the two apart.

**Which side is the single source of truth: the env var in
`tenants/<tenant>.env`.** Not the backend `Settings` field, and not
`PROTON_FEATURES`. Both of those are now *readers* of the same line. The backend
reads it via `env_file:`; Rails reads it because `x-chatwoot-env` interpolates
`${FLAG:-false}`, and `PROTON_FEATURES` is unioned with whatever that yields.

**Two residual asymmetries, tested rather than glossed over.**

1. *The `--env-file` flag is load-bearing.* Compose interpolation reads the shell
   environment and the file passed as `--env-file`, NOT the `env_file:` entries of
   individual services. Deploy the stack without
   `--env-file tenants/<tenant>.env` and the backend still sees the flag (its own
   `env_file:`) while Rails falls back to `:-false` -- which is the original
   two-switch bug, exactly. `test_omitting_the_env_file_recreates_the_two_switch_bug`
   demonstrates that failure with the real Compose CLI, and then asserts that
   every scripted and documented invocation in this repo passes the flag.
2. *The unification is one-directional, deliberately.* Flag on => feature on,
   always. A name hand-added to `PROTON_FEATURES` with the flag off still turns the
   client surface on -- additive, never subtractive, so this cannot switch off a
   tenant that already followed register 3g/3h's advice. The state the register
   complained about (documented flag ON, client surface OFF) is the one that can no
   longer occur; its mirror image can, and errs in the safe direction. For
   `faq_suggestion_popup` the backend field has no consumer at all, so the mirror
   image is inert. For `inbound_alerts` the backend field has exactly one consumer
   -- `surface_freshness`, which labels the alert surface `live_stream` rather than
   `poll_60s` -- and the mirror image makes it *understate* how live the alerting
   is. Understating is the direction this programme's own freshness rule requires;
   the combination the unification removes is the one where the backend claimed
   `live_stream` about a client surface that was switched off. Stated, not tested
   around.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from chatbot.features.metrics.freshness import surface_freshness
from chatbot.platform.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[5]
PATCH_DIR = _REPO_ROOT / "deploy" / "chatwoot-fork" / "patches"
PATCH_0001 = PATCH_DIR / "0001-runtime-config.patch"
PATCH_0058 = PATCH_DIR / "0058-feature-flag-unification.patch"
PATCH_0056 = PATCH_DIR / "0056-faq-composer-apply.patch"
PATCH_0057 = PATCH_DIR / "0057-inbound-alerts.patch"
COMPOSE_PATH = _REPO_ROOT / "deploy" / "docker-compose.tenant.yml"
DEFAULT_ENV_PATH = _REPO_ROOT / "deploy" / "tenants" / "default.env"
ADD_TENANT_SH = _REPO_ROOT / "deploy" / "scripts" / "add-tenant.sh"
README_PATH = _REPO_ROOT / "README.md"

for _p in (
    PATCH_0001,
    PATCH_0058,
    PATCH_0056,
    PATCH_0057,
    COMPOSE_PATH,
    DEFAULT_ENV_PATH,
    ADD_TENANT_SH,
    README_PATH,
):
    assert _p.is_file(), f"not found: {_p}"

LAYOUT_REL_PATH = "app/views/layouts/vueapp.html.erb"

PATCH_0058_TEXT = PATCH_0058.read_text(encoding="utf-8")
COMPOSE_TEXT = COMPOSE_PATH.read_text(encoding="utf-8")

# The flags this task unifies, and the file each is documented in. The FEATURE
# NAME each maps to is deliberately absent -- see the module docstring.
#
# PHONE_AGENT_SOFTPHONE_ENABLED (whole-branch review, Important 4) joined this
# map after the fact -- the in-CRM agent softphone's client gate
# (`protonHasFeature('agent_softphone')`, patch 0069) is the same two-switch
# shape 3g/3h were, and this is the mechanism built to remove it.
_UNIFIED_FLAGS = (
    "INBOUND_ALERTS_ENABLED",
    "FAQ_SUGGESTION_POPUP_ENABLED",
    "PHONE_AGENT_SOFTPHONE_ENABLED",
)
# Some unified flags depend on another Settings field to construct without a
# ValidationError (see config.py's `_phone_flag_dependencies`) -- extra kwargs
# supplied here so `Settings(**{field: value})` below doesn't blow up on a
# structural dependency that has nothing to do with THIS task's assertion.
_EXTRA_SETTINGS_FOR_FLAG: dict[str, dict[str, object]] = {
    "PHONE_AGENT_SOFTPHONE_ENABLED": {
        "phone_handoff_enabled": True,
        "phone_transcript_live_enabled": True,
    },
}

_RUBY = shutil.which("ruby")
_DOCKER = shutil.which("docker")

# The minimum a tenant env file needs for `docker compose config` to resolve this
# compose file at all. Every one of these is already required today; none of them
# is part of what this task changes.
_MINIMAL_TENANT_ENV = {
    "TENANT": "flagtest",
    "SECRET_KEY_BASE": "x",
    "HOST_PREFIX": "",
    "PUBLIC_IP": "203.0.113.7",
    "CHATWOOT_DB_PASSWORD": "p",
    "REDIS_PASSWORD": "r",
}
# The services that must see the flag, and why each one matters. `chatwoot-rails`
# renders the ERB; `chatwoot-sidekiq` shares the anchor and would drift from it if
# only one were wired; `backend` is the reader that was already correct.
_CHATWOOT_SERVICES = ("chatwoot-rails", "chatwoot-sidekiq")
# Everything the compose file interpolates that these tests care about. Removed
# from the subprocess environment so no assertion here can be satisfied by the
# ambient shell -- the flags-ON gate exports both flags.
_INTERPOLATED_VARS = {*_UNIFIED_FLAGS, "PROTON_FEATURES"}


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
def layout_before_0058(tmp_path_factory: pytest.TempPathFactory) -> str:
    """`vueapp.html.erb` with 0001 applied and 0058 deliberately NOT applied.

    Exists so the tests below can be shown to detect the bug rather than merely
    describe the fix: the pre-0058 layout must actually exhibit the two-switch
    behaviour when rendered. A suite that only asserts the new mechanism exists
    would pass identically against a no-op patch.
    """
    tree = tmp_path_factory.mktemp("pre0058-tree")
    path = tree / LAYOUT_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_synthetic_upstream_layout(), encoding="utf-8")
    assert _run(tree, "git", "init", "-q").returncode == 0
    assert _run(tree, "git", "apply", "--include", LAYOUT_REL_PATH, str(PATCH_0001)).returncode == 0
    return path.read_text(encoding="utf-8")


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
        "%w[PROTON_FEATURES INBOUND_ALERTS_ENABLED FAQ_SUGGESTION_POPUP_ENABLED "
        "PHONE_AGENT_SOFTPHONE_ENABLED]"
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

    Matches both the bare `hasFeature(...)` call (0056/0057's own components)
    and the `protonHasFeature(...)` alias (`const { hasFeature: protonHasFeature
    } = useProtonConfig()`, used wherever a component -- Sidebar.vue, mounted by
    0057 and 0069 -- would otherwise collide with Chatwoot's own native
    `hasFeature`). Same composable, same gate, different local name.
    """
    names: set[str] = set()
    for patch in sorted(PATCH_DIR.glob("*.patch")):
        for match in re.finditer(
            r"(?:proton)?[Hh]asFeature\(\s*'([a-z0-9_]+)'\s*\)", patch.read_text(encoding="utf-8")
        ):
            names.add(match.group(1))
    return names


# ---------------------------------------------------------------------------
# Rendering the real compose file with the real Compose CLI
# ---------------------------------------------------------------------------


def _compose_tree(tmp_path: Path, tenant_env: dict[str, str]) -> tuple[Path, Path]:
    """A minimal tree holding the REAL compose file and a synthetic tenant env.

    The compose file is copied, never rewritten, so what Compose parses is the
    shipped bytes. `env_file: tenants/${TENANT}.env` resolves relative to the
    compose file, which is why the tenant env has to live in a `tenants/`
    subdirectory rather than anywhere convenient.
    """
    root = tmp_path / "composetree"
    (root / "tenants").mkdir(parents=True, exist_ok=True)
    compose = root / COMPOSE_PATH.name
    compose.write_bytes(COMPOSE_PATH.read_bytes())
    env = dict(_MINIMAL_TENANT_ENV)
    env.update(tenant_env)
    env_path = root / "tenants" / f"{env['TENANT']}.env"
    env_path.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    return compose, env_path


def _compose_environments(
    tmp_path: Path, tenant_env: dict[str, str], *, pass_env_file: bool
) -> dict[str, dict[str, str]]:
    """Each service's fully resolved environment, per `docker compose config`.

    Skips (never silently passes) without the Compose CLI, so a green run always
    means real Compose resolved this. `--format json` avoids a YAML dependency.
    """
    if _DOCKER is None:  # pragma: no cover - environment dependent
        pytest.skip("the docker CLI is not available; compose resolution cannot be executed here")
    compose, env_path = _compose_tree(tmp_path, tenant_env)
    args = [_DOCKER, "compose", "-f", compose.name]
    if pass_env_file:
        args += ["--env-file", str(Path("tenants") / env_path.name)]
    args += ["config", "--format", "json"]
    # Compose interpolation reads the SHELL environment too, and it OUTRANKS
    # --env-file. The both-flag-states gate exports these very variables, so
    # inheriting os.environ unfiltered would make every assertion below a
    # statement about the ambient environment -- the same trap `Settings()` sets,
    # one layer out. So they are removed, and the removal is asserted.
    shell_env = {k: v for k, v in os.environ.items() if k not in _INTERPOLATED_VARS}
    assert not (_INTERPOLATED_VARS & set(shell_env)), "the ambient flags leaked into the subprocess"
    # Without --env-file there is nothing else to interpolate from, so the
    # unconditionally-required variables have to come from the shell instead --
    # and pointedly NOT the flag, which is what that case exists to show.
    if not pass_env_file:
        shell_env.update(_MINIMAL_TENANT_ENV)
    result = subprocess.run(  # noqa: S603
        args,
        cwd=compose.parent,
        capture_output=True,
        text=True,
        check=False,
        env=shell_env,
    )
    if result.returncode != 0:  # pragma: no cover - environment dependent
        pytest.skip(f"docker compose config could not run here: {result.stderr.strip()[:400]}")
    parsed = json.loads(result.stdout)
    return {
        name: {str(k): str(v) for k, v in (svc.get("environment") or {}).items()}
        for name, svc in parsed["services"].items()
    }


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

    The truthy/falsy corpus here only pins the ERB's own behaviour. Whether it
    AGREES with what the backend does with the same string is a separate
    question, and it is the question the two-switch bug is made of -- so it gets
    its own test below, driven off the real `Settings` rather than a list retyped
    here.
    """
    fragment = _erb_fragment(applied_layout)

    got = _render_features(fragment, {"INBOUND_ALERTS_ENABLED": "true"}, tmp_path)
    assert got == ["inbound_alerts"]
    got = _render_features(fragment, {"FAQ_SUGGESTION_POPUP_ENABLED": "true"}, tmp_path)
    assert got == ["faq_suggestion_popup"]

    for truthy in ("true", "TRUE", "1", "y", "yes", "on"):
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


# ---------------------------------------------------------------------------
# The chain, executed by the real tools rather than reasoned about
# ---------------------------------------------------------------------------


def test_one_line_in_the_tenant_env_file_reaches_the_backend_and_both_chatwoot_services(
    tmp_path: Path,
) -> None:
    """The property the whole task exists for, rendered by the real Compose CLI.

    A single `INBOUND_ALERTS_ENABLED=true` in `tenants/<tenant>.env` -- one line,
    one file, no second setting anywhere -- must arrive in the resolved
    environment of the service that renders the ERB, the Sidekiq service that
    shares its anchor, AND the backend that reads the `Settings` field. That is
    what "one source of truth" means operationally, and it is not something the
    text of the compose file can be grepped for: `x-chatwoot-env` is a YAML anchor
    and `${VAR:-false}` is an interpolation, so only resolving the file proves
    either reached the services.
    """
    envs = _compose_environments(tmp_path, {"INBOUND_ALERTS_ENABLED": "true"}, pass_env_file=True)
    for service in (*_CHATWOOT_SERVICES, "backend"):
        assert envs[service]["INBOUND_ALERTS_ENABLED"] == "true", (
            f"{service} did not receive the flag; one of the two halves of the feature would be off"
        )
    # And the flag the tenant did NOT set stays off everywhere -- so the
    # passthrough is per-flag, not a blanket "enable the lot".
    for service in _CHATWOOT_SERVICES:
        assert envs[service]["FAQ_SUGGESTION_POPUP_ENABLED"] == "false"


def test_a_tenant_with_the_flag_off_gets_the_feature_off_on_both_sides(tmp_path: Path) -> None:
    """No half-on state in the off direction either.

    Both the explicit `false` and the omitted-entirely case, because those are
    different code paths in Compose (`:-` only substitutes for unset-or-empty)
    and every existing tenant is in the second one today.
    """
    for tenant_env in ({"INBOUND_ALERTS_ENABLED": "false"}, {}):
        envs = _compose_environments(tmp_path, tenant_env, pass_env_file=True)
        for service in _CHATWOOT_SERVICES:
            assert envs[service]["INBOUND_ALERTS_ENABLED"] == "false", tenant_env
        # The backend's own view: absent means the field's default, which is False.
        backend_value = envs["backend"].get("INBOUND_ALERTS_ENABLED", "")
        assert backend_value in ("", "false"), backend_value
        assert Settings(inbound_alerts_enabled=backend_value or False).inbound_alerts_enabled is (
            False
        )


def test_the_previously_broken_state_backend_on_and_the_spa_gate_off_can_no_longer_occur(
    applied_layout: str, tmp_path: Path
) -> None:
    """The register's actual complaint, closed end to end.

    Compose resolves the tenant's one line; the value it resolved for
    `chatwoot-rails` is then handed to the shipped ERB; and the ERB's output must
    contain the name `hasFeature()` reads. Nothing between the tenant env file and
    the SPA's gate is re-implemented here -- Compose does the interpolation, Ruby
    does the render, and the feature name comes out of 0057's own source.
    """
    fragment = _erb_fragment(applied_layout)
    for flag, feature in _erb_flag_map(applied_layout).items():
        envs = _compose_environments(tmp_path, {flag: "true"}, pass_env_file=True)
        rails_env = envs["chatwoot-rails"]
        # The backend is on -- that is the premise of the broken state.
        field = flag.lower()
        backend_settings = Settings(
            **{field: envs["backend"][flag]},  # type: ignore[arg-type]
            **_EXTRA_SETTINGS_FOR_FLAG.get(flag, {}),
        )
        assert backend_settings.model_dump()[field] is True
        # ...and the SPA's gate is now on as well, which is what used to fail.
        rendered = _render_features(
            fragment, {k: rails_env[k] for k in _UNIFIED_FLAGS if k in rails_env}, tmp_path
        )
        assert feature in rendered, (
            f"{flag} is on for the backend but the SPA's feature list is {rendered} -- "
            "this is exactly the two-switch state the task closes"
        )


def test_omitting_the_env_file_recreates_the_two_switch_bug(tmp_path: Path) -> None:
    """The residual fragility, demonstrated rather than assumed away.

    Compose interpolation reads the shell environment and `--env-file`; it does
    NOT read the `env_file:` entries of individual services. So a deploy that
    omits `--env-file tenants/<tenant>.env` puts the backend on (its own
    `env_file:` is still honoured) and leaves Rails on the `:-false` default --
    the original bug, reconstructed. This test shows that, then pins the thing
    that prevents it: every scripted and documented invocation passes the flag.
    """
    _, env_path = _compose_tree(tmp_path, {"INBOUND_ALERTS_ENABLED": "true"})
    assert "INBOUND_ALERTS_ENABLED=true" in env_path.read_text(encoding="utf-8")
    envs = _compose_environments(tmp_path, {"INBOUND_ALERTS_ENABLED": "true"}, pass_env_file=False)
    assert envs["backend"]["INBOUND_ALERTS_ENABLED"] == "true", (
        "the backend reads the tenant file directly via env_file:, so it is on"
    )
    for service in _CHATWOOT_SERVICES:
        assert envs[service]["INBOUND_ALERTS_ENABLED"] == "false", (
            "if this ever becomes 'true' the fix no longer depends on --env-file "
            "and the guard below can go"
        )

    # What keeps that from happening: the deploy path always passes the file.
    script = ADD_TENANT_SH.read_text(encoding="utf-8")
    tenant_compose_lines = [
        line for line in script.splitlines() if "docker compose" in line and "TENANT_FILE" in line
    ]
    assert tenant_compose_lines, "add-tenant.sh no longer invokes the tenant compose file"
    for line in tenant_compose_lines:
        assert "--env-file" in line, line

    # And the README, which is what a human follows for the redeploys the script
    # does not do. Its commands are hard-wrapped across lines, some inside
    # blockquotes and some with a trailing backslash, so every newline (with any
    # leading `>` or indent) is joined before looking at them.
    readme = re.sub(r"\\?\n[>\s]*", " ", README_PATH.read_text(encoding="utf-8"))
    documented = 0
    for match in re.finditer(r"docker compose\b", readme):
        window = readme[match.start() : match.start() + 260]
        if "docker-compose.tenant.yml" not in window:
            continue
        documented += 1
        assert "--env-file" in window, (
            "the README documents a tenant deploy without --env-file, which would "
            f"leave the Chatwoot half of a unified flag off: {window[:200]}"
        )
    assert documented >= 3, (
        f"only {documented} tenant deploys found in the README -- the scan is not "
        "reaching the commands it is supposed to be checking"
    )


def test_no_value_of_the_variable_can_leave_the_backend_on_and_the_spa_gate_off(
    applied_layout: str, tmp_path: Path
) -> None:
    """Parity between the ERB's truthy set and the backend's, derived from both.

    The patch header claims the two agree. That claim is what stops a tenant
    writing `y` from re-creating the bug in a third variant, so it is checked
    against the REAL `Settings` field rather than a list of strings retyped from
    pydantic's documentation.

    The invariant asserted is one-directional and deliberately so: **there is no
    value for which the backend is True and the ERB is off.** The reverse is
    allowed and one case really does differ -- see the test below.
    """
    fragment = _erb_fragment(applied_layout)
    corpus = (
        "true",
        "True",
        "TRUE",
        "t",
        "T",
        "yes",
        "Yes",
        "y",
        "Y",
        "on",
        "ON",
        "1",
        "false",
        "False",
        "f",
        "no",
        "n",
        "off",
        "0",
        "",
        "   ",
        "  true  ",
        "maybe",
        "2",
        "-1",
        "enabled",
    )
    divergences: list[str] = []
    for value in corpus:
        try:
            backend_on = Settings(inbound_alerts_enabled=value).inbound_alerts_enabled
        except ValidationError:
            # The backend refuses to start at all on this value, so no running
            # system can be in a half-on state because of it. Loud, not silent.
            continue
        erb_on = "inbound_alerts" in _render_features(
            fragment, {"INBOUND_ALERTS_ENABLED": value}, tmp_path
        )
        if backend_on and not erb_on:
            divergences.append(value)
    assert not divergences, (
        "values for which the backend is enabled but the SPA gate is not -- the "
        f"two-switch bug in a new variant: {divergences}"
    )


def test_a_whitespace_padded_value_fails_loudly_on_the_backend_rather_than_diverging_silently(
    applied_layout: str, tmp_path: Path
) -> None:
    """The one place the two parsers genuinely differ, named for what it is.

    `INBOUND_ALERTS_ENABLED="  true  "` is truthy to the ERB (it strips) and a
    hard `ValidationError` to pydantic (it does not). That is a divergence, but
    not the dangerous kind: the backend does not come up, so an operator gets a
    crash on deploy instead of a feature that is quietly half on. Asserted so
    nobody "fixes" the ERB's strip without noticing what it is compensating for.
    """
    with pytest.raises(ValidationError):
        Settings(inbound_alerts_enabled="  true  ")
    assert "inbound_alerts" in _render_features(
        _erb_fragment(applied_layout), {"INBOUND_ALERTS_ENABLED": "  true  "}, tmp_path
    )


def test_the_single_switch_also_drives_the_one_backend_consumer_the_flag_has() -> None:
    """What the unified switch controls on the server side, stated precisely.

    `faq_suggestion_popup_enabled` has NO backend consumer -- the fork's feature
    list is the entirety of what it does, which is why the unification is the
    whole of that flag's wiring. `inbound_alerts_enabled` has exactly one:
    `surface_freshness` labels the alert surface `live_stream` instead of the
    my-tasks 60-second poll. Before the unification those could disagree, and the
    disagreement was a false freshness claim -- the backend asserting a live
    stream about a client surface that was switched off.
    """
    fields: dict[str, object] = {
        "dashboard_freshness_enabled": True,
        "metrics_api_key": "k",
    }
    on = surface_freshness(Settings(**fields, inbound_alerts_enabled=True))  # type: ignore[arg-type]
    off = surface_freshness(Settings(**fields, inbound_alerts_enabled=False))  # type: ignore[arg-type]
    assert on["alert_stream"] != off["alert_stream"], (
        "inbound_alerts_enabled no longer has a backend consumer; the docstring above is now wrong"
    )
    # And the FAQ flag's claim: no module outside config/test reads the field.
    src_root = Path(__file__).resolve().parent
    readers = [
        path
        for path in src_root.rglob("*.py")
        if "faq_suggestion_popup_enabled" in path.read_text(encoding="utf-8")
        and path.name != "config.py"
        and not path.name.startswith("test_")
    ]
    assert not readers, (
        f"faq_suggestion_popup_enabled now has a backend consumer ({readers}); the "
        "docstring above and the register's section 3g need updating"
    )


def test_these_tests_detect_the_bug_the_pre_0058_layout_really_had(
    layout_before_0058: str, tmp_path: Path
) -> None:
    """The sensitivity check, and the reason to trust the tests above.

    With 0001 applied and 0058 NOT applied, `INBOUND_ALERTS_ENABLED=true` renders
    a feature list that does not contain `inbound_alerts` -- the operator flips the
    documented switch and the SPA's gate stays shut. Same rendering path, same
    Ruby, same env: the only difference is the patch. So the assertions in
    `test_the_previously_broken_state_...` are sensitive to the property and not
    merely to the presence of a Ruby hash.
    """
    marker = layout_before_0058.index("window.__PROTON_CONFIG__")
    start = layout_before_0058.rindex("<script>", 0, marker)
    end = layout_before_0058.index("</script>", marker)
    fragment = layout_before_0058[start : end + len("</script>")]
    assert "proton_flag_features" not in fragment, "0058 leaked into the pre-image fixture"

    assert _render_features(fragment, {"INBOUND_ALERTS_ENABLED": "true"}, tmp_path) == [], (
        "the pre-0058 layout did not have the bug, so these tests prove nothing"
    )
    assert _render_features(
        fragment,
        {"PROTON_FEATURES": "ai_assist", "FAQ_SUGGESTION_POPUP_ENABLED": "true"},
        tmp_path,
    ) == ["ai_assist"]
