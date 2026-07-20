from __future__ import annotations

import pytest

from scraper.classifier import classify


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.proton.com/models/all-new-x50", "model"),
        ("https://www.proton.com/all-new-saga", "model"),
        ("https://www.proton.com/after-sales/owners-manual/x50", "after_sales"),
        ("https://www.proton.com/ownership/offers", "offer"),
        ("https://www.proton.com/about-us", "generic"),
    ],
)
def test_classify(url: str, expected: str) -> None:
    assert classify(url) == expected
