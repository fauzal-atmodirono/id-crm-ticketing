"""How much of a sparse, agent-entered field is actually filled in.

Most of P3's columns are typed by a human under time pressure. A slide grouped
by `vehicle_plate` that shows three bars is not showing the fleet -- it is
showing the three agents who filled the field in. Reporting the number without
reporting its coverage invites exactly the wrong conclusion, so any response
grouped by one of these fields carries a note saying how complete it is.

The distinction the second test guards is the whole point: **0% coverage and
"there were no cases" are different statements.** A slide that prints "0% have
a plate number" when the period contained no cases at all is simply wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CoverageNote:
    """Attach to any report block grouped by a sparse field."""

    field: str
    populated: int
    total: int
    pct: float | None

    @property
    def caption(self) -> str:
        """A sentence the slide can print verbatim."""
        if self.pct is None:
            return f"No cases in this period, so {self.field} coverage is not measurable."
        return (
            f"{self.populated} of {self.total} cases ({self.pct:.0f}%) have "
            f"{self.field} recorded; the rest are excluded from this breakdown."
        )


def _value_of(row: Any, field: str) -> Any:
    if isinstance(row, dict):
        return row.get(field)
    return getattr(row, field, None)


def is_populated(value: Any) -> bool:
    """Whitespace is not data. An agent who typed a space did not fill it in."""
    if value is None:
        return False
    return bool(str(value).strip())


def coverage_pct(rows: list[Any], field: str) -> float | None:
    """Percentage of rows with `field` populated, or None for an empty set.

    None rather than 0.0 deliberately -- see the module docstring.
    """
    if not rows:
        return None
    populated = sum(1 for row in rows if is_populated(_value_of(row, field)))
    return 100.0 * populated / len(rows)


def coverage_note(rows: list[Any], field: str) -> CoverageNote:
    populated = sum(1 for row in rows if is_populated(_value_of(row, field)))
    return CoverageNote(
        field=field,
        populated=populated,
        total=len(rows),
        pct=coverage_pct(rows, field),
    )
