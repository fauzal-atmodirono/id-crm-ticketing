"""Business rules for the UC2 quotation comparison, loaded from business_rules.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class Baseline(BaseModel):
    source: str = "rate_history"
    window_months: int = 3
    statistic: str = "median"


class Flags(BaseModel):
    above_baseline_pct: float
    below_baseline_pct: float
    min_on_time_pct: float
    max_claim_pct: float = 1.5
    min_payment_term_days: int
    min_validity_days: int


class Scoring(BaseModel):
    price: float
    on_time: float
    compliance: float


class Margin(BaseModel):
    basis: str = "sell_price"
    min_pct: float
    target_pct: float


class Rules(BaseModel):
    currency: str = "IDR"
    price_unit: str = "per_trip"
    baseline: Baseline = Baseline()
    flags: Flags
    required_fields: list[str] = []
    scoring: Scoring
    blocking_issues: list[str] = []
    margin: Margin
    decision: str = "human"


def load_rules(path: Path) -> Rules:
    return Rules.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
