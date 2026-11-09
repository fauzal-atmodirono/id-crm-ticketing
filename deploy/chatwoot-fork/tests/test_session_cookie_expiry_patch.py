"""Fork patch 0092: the cw_d_session_info cookie keeps the token's real expiry,
and lifespans under 48 h are refused by the zz_ initializer."""
from pathlib import Path

PATCH = Path(__file__).resolve().parent.parent / "patches" / "0092-session-cookie-real-expiry.patch"


def _text():
    return PATCH.read_text()


def _section(name):
    return _text().split(f"diff --git a/{name}", 1)[1].split("\ndiff --git", 1)[0]


def test_cookie_expires_is_the_date_not_a_day_count():
    s = _section("app/javascript/dashboard/store/utils/api.js")
    assert "-    expires: differenceInDays(expiryDate, new Date())," in s
    assert "+    expires: expiryDate," in s
    assert "-import differenceInDays from 'date-fns/differenceInDays';" in s


def test_lifespan_under_48h_is_refused_with_a_warning():
    s = _section("config/initializers/zz_proton_session.rb")
    assert "hours.positive? && hours < 48" in s
    assert "Rails.logger.warn" in s
    assert "+  DeviseTokenAuth.token_lifespan = hours.hours" in s
    assert "-DeviseTokenAuth.token_lifespan = hours.hours if hours.positive?" in s
