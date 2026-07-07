"""One-time setup: register this service as a Chatwoot agent bot and assign
it to an inbox.

Usage (from the `agent/` directory, with the venv active):

    python -m scripts.register_bot --inbox-id 1

Reads `CHATWOOT_URL` / `CHATWOOT_PLATFORM_TOKEN` / `CHATWOOT_API_TOKEN` /
`CHATWOOT_ACCOUNT_ID` via `app.config.get_settings()`, plus `AGENT_PUBLIC_URL`
(this service's publicly-reachable base URL, e.g. `https://agent.example.com`)
read directly from the environment since it's only needed here, not at
runtime.

Steps:
  1. Create the agent bot via the Platform API
     (`POST /platform/api/v1/agent_bots`), pointed at this service's
     `/webhooks/chatwoot/bot` endpoint.
  2. Read back its `secret` via the account-scoped API (the Platform API's
     create/show response only includes `access_token`, not `secret` — see
     `crm/chatwoot/app/views/platform/api/v1/models/_agent_bot.json.jbuilder`
     vs `crm/chatwoot/app/views/api/v1/models/_agent_bot.json.jbuilder`,
     where `secret` is gated on `Current.account_user&.administrator?`).
  3. Assign the bot to `--inbox-id`
     (`POST /api/v1/accounts/{account}/inboxes/{inbox_id}/set_agent_bot`,
     verified against `crm/chatwoot/config/routes.rb:259`).

Prints `CHATWOOT_BOT_TOKEN` / `CHATWOOT_BOT_SECRET` for you to copy into
`.env` — this script never writes `.env` itself.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

from app.clients.chatwoot import ChatwootClient, ChatwootPlatformClient
from app.config import get_settings

BOT_NAME = "Gemini Agent"


async def _run(inbox_id: int) -> int:
    agent_public_url = os.environ.get("AGENT_PUBLIC_URL")
    if not agent_public_url:
        print(
            "AGENT_PUBLIC_URL is not set -- it must be this service's "
            "publicly-reachable base URL (e.g. https://agent.example.com).",
            file=sys.stderr,
        )
        return 1

    settings = get_settings()
    outgoing_url = f"{agent_public_url.rstrip('/')}/webhooks/chatwoot/bot"

    platform = ChatwootPlatformClient(
        base_url=settings.chatwoot_url,
        platform_token=settings.chatwoot_platform_token,
    )
    account = ChatwootClient(
        base_url=settings.chatwoot_url,
        api_access_token=settings.chatwoot_api_token,
        account_id=settings.chatwoot_account_id,
    )

    try:
        bot = await platform.create_agent_bot(BOT_NAME, outgoing_url)
        bot_id = bot["id"]
        access_token = bot.get("access_token")

        print(f"Created agent bot {bot_id!r} ({BOT_NAME}) -> {outgoing_url}")
        print()
        print(f"CHATWOOT_BOT_TOKEN={access_token}")

        secret = None
        try:
            detail = await account.get_agent_bot(bot_id)
            secret = detail.get("secret")
        except httpx.HTTPError:
            secret = None

        if secret:
            print(f"CHATWOOT_BOT_SECRET={secret}")
        else:
            print(
                "# Could not read the bot's secret automatically (CHATWOOT_API_TOKEN "
                "may not belong to an account administrator). In Chatwoot: "
                "Settings > Integrations > Agent Bots > this bot, copy/reset its "
                "secret, and set CHATWOOT_BOT_SECRET in .env yourself."
            )
        print()
        print("Add the value(s) above to .env, then restart the agent service.")

        await account.set_agent_bot(inbox_id, bot_id)
        print(f"Assigned bot {bot_id} to inbox {inbox_id}.")
    finally:
        await platform.aclose()
        await account.aclose()

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register the Gemini agent bot in Chatwoot and assign it to an inbox."
    )
    parser.add_argument(
        "--inbox-id",
        type=int,
        required=True,
        help="Chatwoot inbox id to assign the bot to.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.inbox_id)))


if __name__ == "__main__":
    main()
