from __future__ import annotations

from urllib.parse import urlparse

from scraper.models import SourceType


def classify(url: str) -> SourceType:
    path = urlparse(url).path.rstrip("/")
    if path.startswith("/models") or path.startswith("/all-new"):
        return "model"
    if path.startswith("/after-sales"):
        return "after_sales"
    if "offer" in path or path.startswith("/ownership"):
        return "offer"
    return "generic"
