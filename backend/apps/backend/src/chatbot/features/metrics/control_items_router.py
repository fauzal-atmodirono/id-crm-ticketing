"""GET /metrics/control-items — the fourteen-row slide, evaluated.

Always fourteen rows. A row with no source reports `no_data` and its reason;
it is never omitted and never rendered as zero. Omitting it would be its own
kind of dishonesty -- the client counts the rows against the printed page.
"""

from __future__ import annotations

import hmac
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Header, HTTPException

from chatbot.features.metrics.attainment import evaluate
from chatbot.features.metrics.control_items import CONTROL_ITEMS
from chatbot.features.metrics.period_query import PeriodQuery, parse_period_or_400

if TYPE_CHECKING:
    from chatbot.features.metrics.targets_store import TargetsStore
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def build_control_items_router(
    targets_store: TargetsStore,
    settings: Settings,
    actuals_provider: Any | None = None,
) -> APIRouter:
    """`actuals_provider(period) -> dict[str, float | None]` supplies measured
    values keyed by control-item number. Absent, or returning None for a key,
    means "not measured" -- which is a legitimate answer here, not an error."""
    router = APIRouter(tags=["metrics"])

    def _require_key(x_api_key: str | None) -> None:
        key = settings.metrics_api_key
        if (
            not key
            or x_api_key is None
            or not hmac.compare_digest(x_api_key.encode(), key.encode())
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @router.get("/metrics/control-items")
    async def control_items(
        period_query: PeriodQuery,
        scope: str = "",
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_key(x_api_key)
        period = parse_period_or_400(period_query)

        actuals: dict[int, float | None] = {}
        if actuals_provider is not None:
            try:
                actuals = await actuals_provider(period, scope)
            except Exception as exc:
                # Every row degrades to no_data, which is the honest outcome
                # of "we could not measure anything this run" -- and is still
                # never reported as missed.
                _log.warning("control_items_actuals_failed", error=str(exc))
                actuals = {}

        rows: list[dict[str, Any]] = []
        for item in CONTROL_ITEMS:
            target = (
                await targets_store.resolve(item.target_key, scope)
                if item.target_key
                else None
            )
            actual = actuals.get(item.number) if item.measurable else None
            attainment = evaluate(actual, target)
            rows.append({
                "number": item.number,
                "label": item.label,
                "measurable": item.measurable,
                "blocking_reason": item.blocking_reason,
                **asdict(attainment),
            })

        measured = sum(1 for r in rows if r["status"] in ("met", "missed"))
        return {
            "items": rows,
            "period": {"from": str(period.start), "to": str(period.end)} if period else None,
            "note": (
                f"{measured} of {len(rows)} control items are measurable today. "
                f"Rows marked 'no_data' are NOT zero and NOT missed targets -- "
                f"they have no source yet; each carries its reason."
            ),
        }

    return router
