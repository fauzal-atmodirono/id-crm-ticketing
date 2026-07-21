"""Read-side for the in-app metrics dashboard: result shapes, port, and mock.

One dataclass per BigQuery view (columns match the view SELECT exactly).
SAFE_DIVIDE / AVG columns are Optional because BigQuery returns NULL when the
denominator is zero or no rows match."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
class AnomalyRow:
    channel: str
    current_volume: int
    baseline_mean: float | None
    baseline_stddev: float | None


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


@dataclass(frozen=True)
class DeptPicRow:
    department: str
    pic: str
    cases: int
    avg_first_response_min: float | None
    avg_resolution_min: float | None
    resolution_rate: float | None


@dataclass(frozen=True)
class ReopenRow:
    dealer: str
    department: str
    pic: str
    cases: int
    reopened: int
    reopen_rate: float | None


@dataclass(frozen=True)
class DepartmentsMetrics:
    dept_pic: list[DeptPicRow]
    reopen: list[ReopenRow]


@dataclass(frozen=True)
class SlaAchievementRow:
    channel: str
    division: str
    with_sla: int
    met: int
    sla_achievement_rate: float | None


@dataclass(frozen=True)
class TasksPerAgentRow:
    agent_id: str
    pic: str
    cases: int
    avg_first_response_min: float | None
    avg_resolution_min: float | None
    resolved_cases: int


@dataclass(frozen=True)
class FirstResponseRow:
    channel: str
    avg_first_response_min: float | None
    p50_first_response_min: int | None
    p90_first_response_min: int | None
    with_first_response: int


@dataclass(frozen=True)
class ResolutionTimeRow:
    channel: str
    division: str
    avg_min: float | None
    p50_min: int | None
    p90_min: int | None


@dataclass(frozen=True)
class ComplaintTypeRow:
    category: str
    subcategory: str
    division: str
    cases: int
    share_pct: float | None


@dataclass(frozen=True)
class PeakHourRow:
    day_of_week: int
    hour_of_day: int
    channel: str
    volume: int


@dataclass(frozen=True)
class NpsByAgentRow:
    agent_id: str
    channel: str
    respondents: int
    nps: float | None


@dataclass(frozen=True)
class CallCentreMetrics:
    sla: list[SlaAchievementRow]
    tasks_per_agent: list[TasksPerAgentRow]
    first_response: list[FirstResponseRow]
    resolution_time: list[ResolutionTimeRow]
    complaint_types: list[ComplaintTypeRow]
    peak_hours: list[PeakHourRow]
    nps_by_agent: list[NpsByAgentRow]


@dataclass(frozen=True)
class CaseLifecycleRow:
    conversation_id: str
    channel: str
    division: str
    department: str
    dealer: str
    status: str
    created_at: datetime | None
    first_response_at: datetime | None
    resolved_at: datetime | None
    first_response_minutes: int | None
    resolution_minutes: int | None
    reopen_count: int | None


@dataclass(frozen=True)
class StateTrendRow:
    month: str
    status: str
    division: str
    cases: int


@dataclass(frozen=True)
class LifecycleMetrics:
    cases: list[CaseLifecycleRow]
    state_trend: list[StateTrendRow]


class MetricsQueryPort(Protocol):
    async def fetch_dashboard(self) -> DashboardMetrics: ...
    async def fetch_anomalies(self) -> list[AnomalyRow]: ...
    async def fetch_departments(self) -> DepartmentsMetrics: ...
    async def fetch_callcenter(self) -> CallCentreMetrics: ...
    async def fetch_lifecycle(self) -> LifecycleMetrics: ...


class MockMetricsQuery:
    """Returns a representative payload so dev/tests never touch BigQuery."""

    async def fetch_anomalies(self) -> list[AnomalyRow]:
        return [
            AnomalyRow("web", current_volume=130, baseline_mean=125.0, baseline_stddev=10.0),
            AnomalyRow("whatsapp", current_volume=260, baseline_mean=90.0, baseline_stddev=15.0),
        ]

    async def fetch_departments(self) -> DepartmentsMetrics:
        return DepartmentsMetrics(
            dept_pic=[DeptPicRow("Aftersales", "Ali", 40, 12.0, 240.0, 0.9)],
            reopen=[ReopenRow("Dealer KL", "Aftersales", "Ali", 40, 4, 0.1)],
        )

    async def fetch_callcenter(self) -> CallCentreMetrics:
        return CallCentreMetrics(
            sla=[SlaAchievementRow("Phone", "Sales", 100, 95, 0.95)],
            tasks_per_agent=[TasksPerAgentRow("ALI001", "Ali", 50, 8.5, 180.0, 48)],
            first_response=[FirstResponseRow("Phone", 8.5, 5, 20, 95)],
            resolution_time=[ResolutionTimeRow("Phone", "Sales", 180.0, 150, 300)],
            complaint_types=[ComplaintTypeRow("Billing", "Late Invoice", "Finance", 25, 0.45)],
            peak_hours=[PeakHourRow(2, 14, "whatsapp", 55)],
            nps_by_agent=[NpsByAgentRow("ALI001", "Phone", 30, 45.0)],
        )

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

    async def fetch_lifecycle(self) -> LifecycleMetrics:
        return LifecycleMetrics(
            cases=[
                CaseLifecycleRow(
                    conversation_id="CONV001",
                    channel="whatsapp",
                    division="Sales",
                    department="Aftersales",
                    dealer="Dealer KL",
                    status="resolved",
                    created_at=None,
                    first_response_at=None,
                    resolved_at=None,
                    first_response_minutes=15,
                    resolution_minutes=240,
                    reopen_count=0,
                )
            ],
            state_trend=[
                StateTrendRow(
                    month="2026-06",
                    status="resolved",
                    division="Sales",
                    cases=45,
                )
            ],
        )
