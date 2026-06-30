"""Read-side for the in-app metrics dashboard: result shapes, port, and mock.

One dataclass per BigQuery view (columns match the view SELECT exactly).
SAFE_DIVIDE / AVG columns are Optional because BigQuery returns NULL when the
denominator is zero or no rows match."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VolumeRow:
    month: str
    channel: str
    volume: int


@dataclass(frozen=True)
class ResolutionRow:
    channel: str
    closed_by_bot: int
    transfer_to_agent: int
    total: int
    closed_by_bot_pct: float | None
    transfer_to_agent_pct: float | None


@dataclass(frozen=True)
class CsatRow:
    channel: str
    respondents: int
    avg_score: float | None
    satisfied_rate: float | None


@dataclass(frozen=True)
class NpsRow:
    channel: str
    respondents: int
    promoters: int
    passives: int
    detractors: int
    nps: float | None


@dataclass(frozen=True)
class SpeedRow:
    channel: str
    is_first_turn: bool
    p99_latency_ms: int | None
    avg_latency_ms: float | None
    turns: int


@dataclass(frozen=True)
class FallbackRow:
    channel: str
    fallback_rate: float | None
    turns: int


@dataclass(frozen=True)
class BounceRow:
    channel: str
    bounced: int
    total_sessions: int
    bounce_rate: float | None


@dataclass(frozen=True)
class QualityRow:
    channel: str
    labels: int
    avg_accuracy: float | None
    avg_quality: float | None


@dataclass(frozen=True)
class DashboardMetrics:
    volume: list[VolumeRow]
    resolution: list[ResolutionRow]
    csat: list[CsatRow]
    nps: list[NpsRow]
    speed: list[SpeedRow]
    fallback: list[FallbackRow]
    bounce: list[BounceRow]
    quality: list[QualityRow]


class MetricsQueryPort(Protocol):
    async def fetch_dashboard(self) -> DashboardMetrics: ...


class MockMetricsQuery:
    """Returns a representative payload so dev/tests never touch BigQuery."""

    async def fetch_dashboard(self) -> DashboardMetrics:
        return DashboardMetrics(
            volume=[
                VolumeRow(month="2026-05", channel="web", volume=120),
                VolumeRow(month="2026-05", channel="whatsapp", volume=80),
                VolumeRow(month="2026-06", channel="web", volume=140),
                VolumeRow(month="2026-06", channel="whatsapp", volume=95),
            ],
            resolution=[
                ResolutionRow("web", 90, 30, 120, 0.75, 0.25),
                ResolutionRow("whatsapp", 60, 20, 80, 0.75, 0.25),
            ],
            csat=[
                CsatRow("web", 40, 4.3, 0.85),
                CsatRow("whatsapp", 25, 4.1, 0.80),
            ],
            nps=[
                NpsRow("web", 35, 20, 10, 5, 42.86),
                NpsRow("whatsapp", 22, 11, 7, 4, 31.82),
            ],
            speed=[
                SpeedRow("web", True, 1800, 950.0, 130),
                SpeedRow("web", False, 1200, 700.0, 410),
                SpeedRow("whatsapp", True, 2100, 1100.0, 90),
                SpeedRow("whatsapp", False, 1500, 820.0, 260),
            ],
            fallback=[
                FallbackRow("web", 0.08, 540),
                FallbackRow("whatsapp", 0.12, 350),
            ],
            bounce=[
                BounceRow("web", 18, 120, 0.15),
                BounceRow("whatsapp", 16, 80, 0.20),
            ],
            quality=[
                QualityRow("web", 20, 88.5, 91.0),
                QualityRow("whatsapp", 15, 84.0, 87.5),
            ],
        )
