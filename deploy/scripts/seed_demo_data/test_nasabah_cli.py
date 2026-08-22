"""The seed-nasabah subcommand's argument surface. Parsing only -- the
network path is exercised by hand against a live tenant, not in CI."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest


def _load_cli():
    """Load __main__.py under a non-`__main__` name.

    A plain `from __main__ import ...` resolves to pytest's own __main__
    module (pytest owns sys.modules['__main__'] when it is the running
    program), so the CLI's helpers are unreachable that way. Loading the
    file by path under its own name is what makes the parser testable at
    all -- every other test in this package imports a module that is not
    the entry point, so this is the first one to need it.
    """
    path = pathlib.Path(__file__).with_name("__main__.py")
    spec = importlib.util.spec_from_file_location("seed_demo_data_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_nasabah_is_a_registered_subcommand():
    parser = _load_cli()._build_parser()
    args = parser.parse_args(["seed-nasabah", "--tenant", "bahana", "--inbox-id", "1"])
    assert args.command == "seed-nasabah"
    assert args.tenant == "bahana"


def test_count_defaults_to_a_demo_sized_batch():
    parser = _load_cli()._build_parser()
    args = parser.parse_args(["seed-nasabah", "--tenant", "bahana", "--inbox-id", "1"])
    assert args.count == 25


def test_pinned_phone_is_optional_and_defaults_to_none():
    parser = _load_cli()._build_parser()
    args = parser.parse_args(["seed-nasabah", "--tenant", "bahana", "--inbox-id", "1"])
    assert args.pinned_phone is None


def test_pinned_phone_and_name_are_accepted():
    parser = _load_cli()._build_parser()
    args = parser.parse_args([
        "seed-nasabah", "--tenant", "bahana", "--inbox-id", "1",
        "--pinned-phone", "+628123456789", "--pinned-name", "Budi Santoso",
    ])
    assert args.pinned_phone == "+628123456789"
    assert args.pinned_name == "Budi Santoso"


def test_inbox_id_is_required():
    parser = _load_cli()._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["seed-nasabah", "--tenant", "bahana"])


def test_nasabah_seed_summary_reports_the_resolved_write_target():
    # Constructed directly against the client's own TenantConfig, not
    # against a live tenant: proves the summary a confirmation-gated write
    # must show (tenant, Chatwoot base URL, account id, inbox id, count,
    # batch id, pinned-phone state) without needing network or async.
    cli = _load_cli()
    parser = cli._build_parser()
    args = parser.parse_args([
        "seed-nasabah", "--tenant", "bahana", "--inbox-id", "42",
        "--chatwoot-url", "https://bahana.example", "--chatwoot-token", "tok",
        "--account-id", "7",
    ])
    config = cli.TenantConfig(
        chatwoot_base_url="https://bahana.example",
        chatwoot_api_access_token="tok",
        chatwoot_account_id=7,
        chatwoot_inbox_id=42,
        backend_base_url="https://backend.example",
        backend_api_key="key",
    )
    lines = cli._nasabah_seed_summary(args, config, batch_id="seed-nasabah-abc123", count=25)
    joined = "\n".join(lines)
    assert "bahana" in joined
    assert "https://bahana.example" in joined
    assert "7" in joined
    assert "42" in joined
    assert "25" in joined
    assert "seed-nasabah-abc123" in joined
    assert "none" in joined.lower()  # no phone pinned


def test_nasabah_seed_summary_shows_the_pinned_phone_when_present():
    cli = _load_cli()
    parser = cli._build_parser()
    args = parser.parse_args([
        "seed-nasabah", "--tenant", "bahana", "--inbox-id", "42",
        "--pinned-phone", "+628123456789",
    ])
    config = cli.TenantConfig(
        chatwoot_base_url="https://bahana.example",
        chatwoot_api_access_token="tok",
        chatwoot_account_id=7,
        chatwoot_inbox_id=42,
        backend_base_url="https://backend.example",
        backend_api_key="key",
    )
    lines = cli._nasabah_seed_summary(args, config, batch_id="b1", count=1)
    assert any("+628123456789" in line for line in lines)
