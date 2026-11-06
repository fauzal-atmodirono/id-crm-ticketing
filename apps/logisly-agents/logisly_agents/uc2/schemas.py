"""Pydantic models for UC2 tender and transporter quotations."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class QuoteLine(BaseModel):
    origin: str
    destination: str
    truck_type: str
    price_idr: int
    unit: Literal["trip", "ton", "kg", "other"] = "trip"
    trips_per_month: int | None = None
    excludes_toll: bool = False
    note: str = ""


class TenderLine(BaseModel):
    origin: str
    destination: str
    truck_type: str
    trips_per_month: int


class Tender(BaseModel):
    reference: str
    customer: str
    lanes: list[TenderLine]
    period: str = ""
    submission_deadline: str = ""
    requirements: list[str] = Field(default_factory=list)
    min_insurance_idr: int | None = None


class Quotation(BaseModel):
    transporter: str
    document_name: str
    document_date: date
    rfq_reference: str = ""
    is_revision: bool = False
    lines: list[QuoteLine]
    payment_term_days: int | None = None
    valid_until: date | None = None
    includes: list[str] = Field(default_factory=list)
    insurance_idr: int | None = None
    gps_tracking: bool | None = None
    notes: str = ""
