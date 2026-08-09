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


class _ScopedMetrics:
    """Per-block `BlockScope`, attached after construction.

    Deliberately NOT a dataclass field. `dataclasses.asdict()` walks only
    *declared* fields, and every `/metrics/*` route serialises its result with
    a bare `asdict(...)` and no response model. A declared `scopes` field would
    therefore add a new top-level key to every response the moment it existed
    -- including on the unfiltered path, breaking "period=None is byte-identical
    to today". Rebinding `_scopes` via `object.__setattr__` keeps that promise
    while still working on a frozen dataclass.

    Extracted in P4 task 2 rather than copied a fifth time: four more metric
    shapes needed exactly this, and the reasoning above is the part worth
    having in one place.
    """

    def __post_init__(self) -> None:
        object.__setattr__(self, "_scopes", {})

    @property
    def scopes(self) -> dict[str, BlockScope]:
        return self._scopes  # type: ignore[attr-defined,no-any-return]

    def attach_scopes(self, scopes: dict[str, BlockScope]) -> None:
        object.__setattr__(self, "_scopes", scopes)


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
    #
    # `metadata={"period_only": True}` marks this field as structurally
    # unpopulatable outside a period-scoped query (see export.py's
    # `_exportable_field_names`, Task 2 review round 3) -- it's a property
    # of the field's *shape*, not of any particular export's data, which
    # is what lets export.py drop it deterministically without also
    # dropping a legitimately-nullable business column (e.g. CsatRow.
    # avg_score) that merely happens to be null on every row of a given
    # week's data.
    month: str
    channel: str
    volume: int
    bucket: str | None = field(default=None, metadata={"period_only": True})


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "_scopes", {})

    @property
    def scopes(self) -> dict[str, BlockScope]:
        """Per-block BlockScope, keyed by field name above. Only `volume`
        can ever be period-scoped; every other key is always "unfiltered"
        -- Critical-1 from the Task 2 review.

        Deliberately NOT a dataclass field: `dataclasses.asdict()`/
        `fields()` only walk *declared* fields, and every current
        `/metrics/*` route serialises this type with a bare
        `asdict(await port.fetch_*())` and no response-model filtering. A
        declared `scopes` field would add a new top-level JSON key to
        those routes' responses *today*, on the unfiltered path, before
        Task 3 wires any period support in at all -- breaking "period=None
        is byte-identical to today" (Task 2 review, the Important finding
        that follows Critical 1). Populated via `attach_scopes()` after
        construction, which is legal on a frozen dataclass because it
        rebinds `_scopes` via `object.__setattr__` exactly once, in
        `__post_init__` and again here -- never by mutating in place.
        """
        return self._scopes  # type: ignore[attr-defined,no-any-return]

    def attach_scopes(self, scopes: dict[str, BlockScope]) -> None:
        object.__setattr__(self, "_scopes", scopes)


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
class CallCentreMetrics(_ScopedMetrics):
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
    #
    # Populated ONLY on the unfiltered path (`v_state_trend` selects it);
    # `None` on the period path, which reads the day-grain sibling and has
    # no calendar-month concept to report. Deliberately NOT marked
    # `period_only` -- that metadata means "structurally unpopulatable in an
    # export" (see export.py's `_exportable_field_names`), and no export
    # route ever supplies a period, so `month_start` is always genuinely
    # populated wherever it is exported. It is a real column, not a blank
    # one (Package E final fix, finding M2).
    month_start: date | None = None
    # Granularity-neutral grouping key, mirroring `VolumeRow.bucket`
    # (Package E final fix, finding M6). Same value as `month` on the
    # period path -- a day/week/month bucket key from `bucket_key()`'s
    # vocabulary -- and `None` on the unfiltered path, where `month` is
    # always a real "YYYY-MM" and the sibling would be redundant. A
    # period-scoped consumer must group by `bucket`, not by `month`:
    # `month` is a month key on one path and a week key like "2026-W29" on
    # the other, and the field cannot be renamed because
    # `0020-reports-native-merge.patch`'s state-trend chart reads it
    # positionally as a month. Marked `period_only` for the same reason
    # `VolumeRow.bucket` is: no export path supplies a period, so it could
    # never be anything but blank there.
    bucket: str | None = field(default=None, metadata={"period_only": True})


@dataclass(frozen=True)
class LifecycleMetrics:
    cases: list[CaseLifecycleRow]
    state_trend: list[StateTrendRow]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_scopes", {})

    @property
    def scopes(self) -> dict[str, BlockScope]:
        """See DashboardMetrics.scopes (same not-a-field rationale).
        `cases` is "unfiltered" on the no-period path and
        "unsupported_granularity" with an empty row list once a period is
        supplied -- `v_case_lifecycle` is a row-per-case view with no
        aggregate grain a period could be honoured at, so the adapter
        skips the query entirely rather than full-scanning it twice per
        page load and serialising the whole all-time case list under a
        week header (Package E final fix, finding I5). `state_trend` can
        be "ok"/"unavailable"/"unsupported_granularity" once a period is
        supplied."""
        return self._scopes  # type: ignore[attr-defined,no-any-return]

    def attach_scopes(self, scopes: dict[str, BlockScope]) -> None:
        object.__setattr__(self, "_scopes", scopes)


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
class DealerEscalationMetrics(_ScopedMetrics):
    by_dealer: list[DealerEscalationRow]
    slowest_cases: list[DealerSlowCaseRow]


@dataclass(frozen=True)
class SlaBucketRow:
    case_type: str
    bucket_label: str | None
    cases: int


@dataclass(frozen=True)
class SlaBucketMetrics(_ScopedMetrics):
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
class CaseAgingMetrics(_ScopedMetrics):
    cases: list[CaseAgingRow]


@dataclass(frozen=True)
class AfterHoursVolumeRow:
    """One row of `v_volume_after_hours`.

    `arrival_window` is always one of `in_hours` / `after_hours` / `unknown` --
    the third exists because rows synced before P1 carry no intake stamp, and
    counting them as after-hours would invent an out-of-hours problem.
    """

    month: str
    channel: str
    arrival_window: str
    volume: int
    # See StateTrendRow.month_start — same widening, same reason.
    month_start: date | None = None
    bucket: str | None = field(default=None, metadata={"period_only": True})


@dataclass(frozen=True)
class AfterHoursFirstResponseRow:
    """One row of `v_first_response_by_hours_split`."""

    month: str
    channel: str
    arrival_window: str
    cases: int
    avg_first_response_working_min: float | None = None
    p90_first_response_working_min: float | None = None


@dataclass(frozen=True)
class AfterHoursMetrics:
    volume: list[AfterHoursVolumeRow]
    first_response: list[AfterHoursFirstResponseRow]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_scopes", {})

    @property
    def scopes(self) -> dict[str, BlockScope]:
        """See DashboardMetrics.scopes (same not-a-field rationale)."""
        return self._scopes  # type: ignore[attr-defined,no-any-return]

    def attach_scopes(self, scopes: dict[str, BlockScope]) -> None:
        object.__setattr__(self, "_scopes", scopes)


@dataclass(frozen=True)
class TagVolumeRow:
    month: str
    tag: str
    channel: str
    cases: int
    month_start: date | None = None
    bucket: str | None = field(default=None, metadata={"period_only": True})


@dataclass(frozen=True)
class TagVolumeMetrics(_ScopedMetrics):
    by_tag: list[TagVolumeRow]

    @property
    def note(self) -> str:
        """Printed with the block, not left to the reader to work out.

        A case with three labels is in three buckets, so summing `cases` gives
        a number larger than the case count -- and a case with no labels is not
        here at all. Both are correct for a tag breakdown and both make the
        column un-summable, which a slide will otherwise do confidently.
        """
        return (
            "Cases are counted once per label, so these figures overlap and do "
            "not sum to the total case count; cases with no label are excluded."
        )


@dataclass(frozen=True)
class VolumeByTypeDivisionRow:
    month: str
    channel: str
    case_type: str
    division: str
    volume: int
    # See StateTrendRow.month_start — same widening, same reason.
    month_start: date | None = None
    # See StateTrendRow.bucket — same granularity-neutral sibling, same
    # reason (Package E final fix, finding M6).
    bucket: str | None = field(default=None, metadata={"period_only": True})


@dataclass(frozen=True)
class VolumeByTypeDivisionMetrics:
    volume: list[VolumeByTypeDivisionRow]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_scopes", {})

    @property
    def scopes(self) -> dict[str, BlockScope]:
        """See DashboardMetrics.scopes (same not-a-field rationale)."""
        return self._scopes  # type: ignore[attr-defined,no-any-return]

    def attach_scopes(self, scopes: dict[str, BlockScope]) -> None:
        object.__setattr__(self, "_scopes", scopes)


@dataclass(frozen=True)
class CategoryByVehicleModelRow:
    category: str
    subcategory: str
    vehicle_model: str
    case_type: str
    cases: int


@dataclass(frozen=True)
class DepartmentsMetrics(_ScopedMetrics):
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
    async def fetch_departments(
        self, period: PeriodRange | None = None, filters: object | None = None
    ) -> DepartmentsMetrics: ...
    async def fetch_callcenter(
        self, period: PeriodRange | None = None, filters: object | None = None
    ) -> CallCentreMetrics: ...
    async def fetch_lifecycle(self, period: PeriodRange | None = None) -> LifecycleMetrics: ...
    async def fetch_dealer_escalation(
        self, period: PeriodRange | None = None, filters: object | None = None
    ) -> DealerEscalationMetrics: ...
    async def fetch_sla_buckets(
        self, period: PeriodRange | None = None, filters: object | None = None
    ) -> SlaBucketMetrics: ...
    async def fetch_case_aging(
        self, period: PeriodRange | None = None, filters: object | None = None
    ) -> CaseAgingMetrics: ...
    async def fetch_volume_by_type_division(
        self, period: PeriodRange | None = None
    ) -> VolumeByTypeDivisionMetrics: ...
    async def fetch_after_hours(
        self, period: PeriodRange | None = None
    ) -> AfterHoursMetrics: ...
    async def fetch_by_tag(
        self, period: PeriodRange | None = None, tag: str | None = None
    ) -> TagVolumeMetrics: ...


_UNFILTERED_SCOPE = BlockScope(status="unfiltered", period=None, supported_granularity=None)
_DEGRADED_SCOPE = BlockScope(status="unavailable", period=None, supported_granularity=None)


class MockMetricsQuery:
    """Returns a representative payload so dev/tests never touch BigQuery.

    Two callers construct this, and they mean opposite things (Package E
    final fix, finding I6):

    - `metrics_provider != "bigquery"` -- a *deliberate* choice to run on
      canned data (local dev, tests, a tenant with no warehouse). The rows
      are the intended answer, and every block reports "unfiltered": they
      are all-time figures, honestly labelled as such.
    - `build_metrics_query_port`'s **fail-open fallback** after
      `bigquery.Client()` init raises (`degraded=True`). Here the canned
      rows are not an answer at all -- 682 cases dated "2026-06" is an
      invented number that renders as a perfectly plausible all-time total
      on a client-facing page. Those blocks report "unavailable" instead,
      which is what a period-scoped consumer already renders as
      "temporarily unavailable" rather than as data. The posture stays
      fail-open -- a misconfigured warehouse must not 500 the page -- but
      it no longer fails open *into fabricated figures*.

    Neither mode ever applies a `period` to the rows themselves; the rows
    are canned either way. `scopes` is the only channel that can say so,
    which is why it is not enough for this class to merely satisfy the
    Protocol's return type.

    Five methods -- `fetch_departments`, `fetch_callcenter`,
    `fetch_dealer_escalation`, `fetch_sla_buckets`, `fetch_case_aging` --
    have no scopes channel at all (their `*Metrics` types carry no
    `attach_scopes`/`scopes`), and giving them one would change the
    response shape the deployed SPA already reads. So on `degraded=True`
    they take the only honest option left: return their `*Metrics` with
    every list empty instead of the canned rows, so a misconfigured
    tenant's Weekly Report renders those five sections as "no data" rather
    than as someone else's fabricated figures. `degraded=False` (the
    deliberate-mock path) is unaffected -- those rows are still the
    intended dev/test answer.
    """

    def __init__(self, *, degraded: bool = False) -> None:
        self._scope = _DEGRADED_SCOPE if degraded else _UNFILTERED_SCOPE
        self._degraded = degraded

    async def fetch_anomalies(self) -> list[AnomalyRow]:
        return [
            AnomalyRow("web", current_volume=130, baseline_mean=125.0, baseline_stddev=10.0),
            AnomalyRow("whatsapp", current_volume=260, baseline_mean=90.0, baseline_stddev=15.0),
        ]

    async def fetch_departments(self, period: PeriodRange | None = None, filters: object | None = None) -> DepartmentsMetrics:
        if self._degraded:
            return DepartmentsMetrics(dept_pic=[], reopen=[], category_by_vehicle_model=[])
        return DepartmentsMetrics(
            dept_pic=[DeptPicRow("Aftersales", "Ali", 40, 12.0, 240.0, 0.9)],
            reopen=[ReopenRow("Dealer KL", "Aftersales", "Ali", 40, 4, 0.1)],
            category_by_vehicle_model=[
                CategoryByVehicleModelRow("Charging", "Home Charging", "e.MAS 5", "Complaint", 12)
            ],
        )

    async def fetch_callcenter(self, period: PeriodRange | None = None, filters: object | None = None) -> CallCentreMetrics:
        if self._degraded:
            return CallCentreMetrics(
                sla=[],
                tasks_per_agent=[],
                first_response=[],
                resolution_time=[],
                complaint_types=[],
                peak_hours=[],
                nps_by_agent=[],
            )
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
        metrics = DashboardMetrics(
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
        metrics.attach_scopes(
            dict.fromkeys(
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
                self._scope,
            )
        )
        return metrics

    async def fetch_lifecycle(self, period: PeriodRange | None = None) -> LifecycleMetrics:
        del period  # mock always returns the same canned payload
        metrics = LifecycleMetrics(
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
        metrics.attach_scopes({"cases": self._scope, "state_trend": self._scope})
        return metrics

    async def fetch_dealer_escalation(self, period: PeriodRange | None = None, filters: object | None = None) -> DealerEscalationMetrics:
        if self._degraded:
            return DealerEscalationMetrics(by_dealer=[], slowest_cases=[])
        return DealerEscalationMetrics(
            by_dealer=[DealerEscalationRow("Dealer KL", 12, 3.5, 3.0, 6.0)],
            slowest_cases=[DealerSlowCaseRow("CONV042", "Dealer KL", 12.0)],
        )

    async def fetch_sla_buckets(self, period: PeriodRange | None = None, filters: object | None = None) -> SlaBucketMetrics:
        if self._degraded:
            return SlaBucketMetrics(buckets=[])
        return SlaBucketMetrics(
            buckets=[
                SlaBucketRow("Inquiry", "Within 8wh", 887),
                SlaBucketRow("Inquiry", ">8wh", 137),
                SlaBucketRow("Complaint", "<24wh", 378),
                SlaBucketRow("Complaint", ">72wh", 290),
            ]
        )

    async def fetch_case_aging(self, period: PeriodRange | None = None, filters: object | None = None) -> CaseAgingMetrics:
        if self._degraded:
            return CaseAgingMetrics(cases=[])
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
        metrics = VolumeByTypeDivisionMetrics(
            volume=[VolumeByTypeDivisionRow("2026-06", "WhatsApp", "Inquiry", "Sales", 682)]
        )
        metrics.attach_scopes({"volume": self._scope})
        return metrics

    async def fetch_by_tag(
        self, period: PeriodRange | None = None, tag: str | None = None
    ) -> TagVolumeMetrics:
        del period
        rows = [
            TagVolumeRow("2026-06", "dept_sales", "WhatsApp", 120),
            TagVolumeRow("2026-06", "escalate", "WhatsApp", 18),
        ]
        if tag is not None:
            rows = [r for r in rows if r.tag == tag]
        metrics = TagVolumeMetrics(by_tag=rows)
        metrics.attach_scopes({"by_tag": self._scope})
        return metrics

    async def fetch_after_hours(
        self, period: PeriodRange | None = None
    ) -> AfterHoursMetrics:
        del period  # mock always returns the same canned payload
        metrics = AfterHoursMetrics(
            volume=[
                AfterHoursVolumeRow("2026-06", "WhatsApp", "in_hours", 480),
                AfterHoursVolumeRow("2026-06", "WhatsApp", "after_hours", 202),
            ],
            first_response=[
                AfterHoursFirstResponseRow("2026-06", "WhatsApp", "in_hours", 480, 12.5, 41.0),
                AfterHoursFirstResponseRow(
                    "2026-06", "WhatsApp", "after_hours", 202, 18.0, 63.0
                ),
            ],
        )
        metrics.attach_scopes(
            {"volume": self._scope, "first_response": self._scope}
        )
        return metrics
