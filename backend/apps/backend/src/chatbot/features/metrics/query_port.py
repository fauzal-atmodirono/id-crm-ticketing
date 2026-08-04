"""Read-side for the in-app metrics dashboard: result shapes, port, and mock.

One dataclass per BigQuery view (columns match the view SELECT exactly).
SAFE_DIVIDE / AVG columns are Optional because BigQuery returns NULL when the
denominator is zero or no rows match."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from chatbot.features.metrics.period import PeriodRange


@dataclass(frozen=True)
class BlockScope:
    """Which period (if any) a block's rows are actually scoped to.

    Rows alone collapse four different states a period-scoped page needs to
    tell apart, all of which look identical as an empty or non-empty list:

    - "ok": a period was applied and the query ran; `rows` may legitimately
      be empty (zero matching cases that week).
    - "unavailable": the query failed (drifted view, a widened column not
      yet rolled out by `ensure_views()` on this deployment, a BigQuery
      outage). `rows` is `[]`, but that's a failure, not a true zero.
    - "unsupported_granularity": the view can't honour the requested period
      at all (e.g. a month-grain view asked for a partial-month or
      sub-month window) -- the query was never run, rather than risk
      either dropping data that doesn't align to the view's grain or, worse,
      silently returning a whole month's total for a partial-month ask.
    - "unfiltered": no period was applied (either none was requested, or
      -- for `fetch_dashboard`'s non-volume blocks -- the block has no
      date column to filter on at all). `rows` are all-time, not scoped to
      any requested period. Without this marker, mixing an unfiltered
      block into a period-scoped page (e.g. "17-23 July") silently
      presents all-time figures as if they belonged to that week.
    """

    status: Literal["ok", "unavailable", "unsupported_granularity", "unfiltered"]
    period: PeriodRange | None  # the period actually applied, if any
    supported_granularity: str | None  # set on "unsupported_granularity"; the grain that would work


@dataclass(frozen=True)
class VolumeRow:
    # NOT renamed to a granularity-neutral name despite holding a week key
    # like "2026-W29" when period-scoped at week granularity (see
    # query_adapter.py's `_volume_block_for_period`): the fork's Overview
    # chart (patch 0020) reads this exact field as `idx[row.month][...]`
    # for the unfiltered dashboard, where it is always a real "YYYY-MM"
    # month. Renaming it would break that live consumer for the one case
    # that must never change. `bucket` is the granularity-neutral sibling
    # for period-scoped callers (Task 4) to read instead -- same value as
    # `month`, `None` only for unfiltered/mock rows where it's redundant.
    month: str
    channel: str
    volume: int
    bucket: str | None = None


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
    # Keyed by field name above. Only `volume` can ever be period-scoped
    # (see BlockScope); every other key is always "unfiltered" -- Critical-1
    # from the Task 2 review. Default empty so a caller that doesn't care
    # (e.g. the unmodified `/metrics/dashboard` route) sees no change.
    scopes: dict[str, BlockScope] = field(default_factory=dict)


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
    # Added for range-aware queries (Package E / Task 2): the calendar month's
    # first day as a real DATE, alongside the pre-existing formatted `month`
    # string, so a period filter can use a BigQuery named parameter instead of
    # string-matching `month`. Optional/trailing so old positional callers and
    # rows from the pre-widening view (missing the column) still construct.
    month_start: date | None = None


@dataclass(frozen=True)
class LifecycleMetrics:
    cases: list[CaseLifecycleRow]
    state_trend: list[StateTrendRow]
    # See DashboardMetrics.scopes. `cases` is always "unfiltered" (no period
    # support at all); `state_trend` can be "ok"/"unavailable"/
    # "unsupported_granularity" once a period is supplied.
    scopes: dict[str, BlockScope] = field(default_factory=dict)


@dataclass(frozen=True)
class DealerEscalationRow:
    dealer: str
    cases_escalated: int
    avg_turnaround_days: float | None
    p50_turnaround_days: float | None
    p90_turnaround_days: float | None


@dataclass(frozen=True)
class DealerSlowCaseRow:
    conversation_id: str
    dealer: str
    turnaround_days: float | None


@dataclass(frozen=True)
class DealerEscalationMetrics:
    by_dealer: list[DealerEscalationRow]
    slowest_cases: list[DealerSlowCaseRow]


@dataclass(frozen=True)
class SlaBucketRow:
    case_type: str
    bucket_label: str | None
    cases: int


@dataclass(frozen=True)
class SlaBucketMetrics:
    buckets: list[SlaBucketRow]


@dataclass(frozen=True)
class CaseAgingRow:
    conversation_id: str
    case_type: str
    division: str
    dealer: str
    pic: str
    status: str
    created_at: datetime | None
    age_days: float | None
    bucket_label: str


@dataclass(frozen=True)
class CaseAgingMetrics:
    cases: list[CaseAgingRow]


@dataclass(frozen=True)
class VolumeByTypeDivisionRow:
    month: str
    channel: str
    case_type: str
    division: str
    volume: int
    # See StateTrendRow.month_start — same widening, same reason.
    month_start: date | None = None


@dataclass(frozen=True)
class VolumeByTypeDivisionMetrics:
    volume: list[VolumeByTypeDivisionRow]
    # See DashboardMetrics.scopes.
    scopes: dict[str, BlockScope] = field(default_factory=dict)


@dataclass(frozen=True)
class CategoryByVehicleModelRow:
    category: str
    subcategory: str
    vehicle_model: str
    case_type: str
    cases: int


@dataclass(frozen=True)
class DepartmentsMetrics:
    dept_pic: list[DeptPicRow]
    reopen: list[ReopenRow]
    category_by_vehicle_model: list[CategoryByVehicleModelRow]


class MetricsQueryPort(Protocol):
    # `period=None` is "today's behaviour": unfiltered, all-time. Only the
    # three methods below carry a real date-bearing view (volume, state
    # trend, volume-by-type/division) so only they take `period`. The other
    # views group by channel/agent/dealer/etc. with no date dimension at
    # all -- there is nothing for a period to filter yet (see G2/G3/G6 in
    # the Package E spec), so adding an always-ignored `period` param to
    # them would be a dishonest API surface rather than a real capability.
    async def fetch_dashboard(self, period: PeriodRange | None = None) -> DashboardMetrics: ...
    async def fetch_anomalies(self) -> list[AnomalyRow]: ...
    async def fetch_departments(self) -> DepartmentsMetrics: ...
    async def fetch_callcenter(self) -> CallCentreMetrics: ...
    async def fetch_lifecycle(self, period: PeriodRange | None = None) -> LifecycleMetrics: ...
    async def fetch_dealer_escalation(self) -> DealerEscalationMetrics: ...
    async def fetch_sla_buckets(self) -> SlaBucketMetrics: ...
    async def fetch_case_aging(self) -> CaseAgingMetrics: ...
    async def fetch_volume_by_type_division(
        self, period: PeriodRange | None = None
    ) -> VolumeByTypeDivisionMetrics: ...


_UNFILTERED_SCOPE = BlockScope(status="unfiltered", period=None, supported_granularity=None)


class MockMetricsQuery:
    """Returns a representative payload so dev/tests never touch BigQuery.

    Every block is reported "unfiltered" regardless of whether a `period`
    was requested: this is also the fail-open fallback when the real
    BigQuery client fails to init (`build_metrics_query_port`), so a period
    request that silently downgrades to this canned all-time payload must
    say so via `scopes`, not just via matching the Protocol's return type.
    """

    async def fetch_anomalies(self) -> list[AnomalyRow]:
        return [
            AnomalyRow("web", current_volume=130, baseline_mean=125.0, baseline_stddev=10.0),
            AnomalyRow("whatsapp", current_volume=260, baseline_mean=90.0, baseline_stddev=15.0),
        ]

    async def fetch_departments(self) -> DepartmentsMetrics:
        return DepartmentsMetrics(
            dept_pic=[DeptPicRow("Aftersales", "Ali", 40, 12.0, 240.0, 0.9)],
            reopen=[ReopenRow("Dealer KL", "Aftersales", "Ali", 40, 4, 0.1)],
            category_by_vehicle_model=[
                CategoryByVehicleModelRow("Charging", "Home Charging", "e.MAS 5", "Complaint", 12)
            ],
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

    async def fetch_dashboard(self, period: PeriodRange | None = None) -> DashboardMetrics:
        del period  # mock always returns the same canned payload
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
            scopes=dict.fromkeys(
                (
                    "volume",
                    "resolution",
                    "csat",
                    "nps",
                    "speed",
                    "fallback",
                    "bounce",
                    "quality",
                ),
                _UNFILTERED_SCOPE,
            ),
        )

    async def fetch_lifecycle(self, period: PeriodRange | None = None) -> LifecycleMetrics:
        del period  # mock always returns the same canned payload
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
            scopes={"cases": _UNFILTERED_SCOPE, "state_trend": _UNFILTERED_SCOPE},
        )

    async def fetch_dealer_escalation(self) -> DealerEscalationMetrics:
        return DealerEscalationMetrics(
            by_dealer=[DealerEscalationRow("Dealer KL", 12, 3.5, 3.0, 6.0)],
            slowest_cases=[DealerSlowCaseRow("CONV042", "Dealer KL", 12.0)],
        )

    async def fetch_sla_buckets(self) -> SlaBucketMetrics:
        return SlaBucketMetrics(
            buckets=[
                SlaBucketRow("Inquiry", "Within 8wh", 887),
                SlaBucketRow("Inquiry", ">8wh", 137),
                SlaBucketRow("Complaint", "<24wh", 378),
                SlaBucketRow("Complaint", ">72wh", 290),
            ]
        )

    async def fetch_case_aging(self) -> CaseAgingMetrics:
        return CaseAgingMetrics(
            cases=[
                CaseAgingRow(
                    "CONV099", "Complaint", "Sales", "Dealer KL", "Ali", "open",
                    created_at=None, age_days=4.0, bucket_label="4-6 days",
                )
            ]
        )

    async def fetch_volume_by_type_division(
        self, period: PeriodRange | None = None
    ) -> VolumeByTypeDivisionMetrics:
        del period  # mock always returns the same canned payload
        return VolumeByTypeDivisionMetrics(
            volume=[VolumeByTypeDivisionRow("2026-06", "WhatsApp", "Inquiry", "Sales", 682)],
            scopes={"volume": _UNFILTERED_SCOPE},
        )
