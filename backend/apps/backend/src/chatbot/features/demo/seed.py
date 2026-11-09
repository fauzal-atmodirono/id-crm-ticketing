"""CLI: seed / reset / show the [DEMO] personas on this backend's tenant.

    python -m chatbot.features.demo.seed            # seed (asks to confirm)
    python -m chatbot.features.demo.seed --reset    # remove demo records, re-seed
    python -m chatbot.features.demo.seed --status   # read-only: what is there now
    ... --yes                                       # no prompt (non-interactive)

Uses the backend's own settings (the tenant env file), so on the VM it runs
inside the tenant's backend container and targets exactly that tenant's
Chatwoot account / API inbox. Refuses unless ``DEMO_CONSOLE_ENABLED=true``
-- the same switch as the Demo tab -- so it cannot be run by accident on a
tenant that never opted in. Prints the target (Chatwoot host, account,
inbox), never the token. Exit codes: 0 done, 1 not confirmed, 2 flag off,
3 refused by a safety check, 4 CRM error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from chatbot.features.demo.crm import ChatwootError
from chatbot.features.demo.safety import DemoSafetyError
from chatbot.features.demo.seeder import build_seeder_factory

if TYPE_CHECKING:
    from chatbot.platform.config import Settings


def _rsa_repo(settings: Settings) -> Any:
    if not (settings.rsa_enabled and settings.rsa_database_url):
        return None
    from chatbot.features.rsa.rsa_db import build_engine, build_session_maker  # noqa: PLC0415
    from chatbot.features.rsa.rsa_repository import PgRsaRepository  # noqa: PLC0415

    return PgRsaRepository(build_session_maker(build_engine(settings.rsa_database_url)))


async def _run(action: str, factory: Any) -> Any:
    async with factory() as seeder:
        if action == "status":
            return await seeder.status()
        if action == "reset":
            return await seeder.reset()
        return await seeder.seed()


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    seeder_factory: Any = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m chatbot.features.demo.seed")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reset", action="store_true", help="remove every demo record, then seed")
    mode.add_argument("--status", action="store_true", help="read-only listing")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)

    if settings is None:
        from chatbot.platform.config import get_settings  # noqa: PLC0415

        settings = get_settings()
    if not settings.demo_console_enabled:
        print("DEMO_CONSOLE_ENABLED is not true for this tenant; refusing.", file=sys.stderr)
        return 2

    action = "reset" if args.reset else "status" if args.status else "seed"
    inbox = settings.demo_inbox_id or settings.chatwoot_inbox_id
    host = urlparse(settings.chatwoot_api_url).netloc or settings.chatwoot_api_url
    print(f"Demo {action}: Chatwoot {host}, account {settings.chatwoot_account_id}, inbox {inbox}")
    if action != "status" and not args.yes:
        answer = input(f"Type {action.upper()} to {action} the [DEMO] personas: ")
        if answer.strip() != action.upper():
            print("Not confirmed; nothing written.")
            return 1

    factory = seeder_factory or build_seeder_factory(settings, _rsa_repo(settings))
    try:
        result = asyncio.run(_run(action, factory))
    except DemoSafetyError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 3
    except ChatwootError as exc:
        print(f"CRM error: {exc}", file=sys.stderr)
        return 4
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
