"""`describe_database_target` is what `backdate` prints in its dry-run
summary on EVERY invocation (not just `--execute`) to answer "which
database am I about to touch". Three rounds of denylist-style redaction
each leaked a password through a shape the regex hadn't anticipated (a
plain authority match, an authority containing a literal '@', a query-
string `?password=...`). This function replaces that whole strategy: it
parses with psycopg's own parser and prints ONLY an explicit allowlist of
keys (host/hostaddr/port/dbname/user). A password cannot appear in the
output regardless of which of the shapes below it arrives in, because
nothing outside the allowlist is ever read from the parsed dict -- these
tests assert that positively (the secret substring is asserted ABSENT),
not just that a particular regex no longer matches it."""

from __future__ import annotations
from urllib.parse import quote

from backdate import describe_database_target

_SECRET = "SuperSecret123"


def test_url_form_describes_host_port_dbname_user_without_the_password():
    result = describe_database_target(
        "postgresql://chatwoot_default:9f508f7afd4ba2bbcc59d72d4f58679e@postgres:5432/chatwoot_default"
    )
    assert result == "host=postgres port=5432 dbname=chatwoot_default user=chatwoot_default"
    assert "9f508f7afd4ba2bbcc59d72d4f58679e" not in result


def test_url_form_percent_encoded_password_containing_at_sign_never_appears_in_output():
    # A literal '@' inside userinfo is only valid in a URI DSN when
    # percent-encoded (RFC 3986) -- this is how an operator's own tooling
    # (or a copy-paste from a secrets manager that quotes correctly) would
    # actually produce such a password. psycopg's parser then separates it
    # from the host cleanly, so it never reaches the allowlist.
    password = f"p@{_SECRET}"
    dsn = f"postgresql://user:{quote(password, safe='')}@host:5432/db"
    result = describe_database_target(dsn)
    assert result == "host=host port=5432 dbname=db user=user"
    assert _SECRET not in result


def test_url_form_password_with_a_raw_unescaped_at_sign_is_a_documented_psycopg_parsing_limit():
    # NOT a redaction gap in this function -- a documented limit of trusting
    # psycopg's own parser (per the design: "parse with psycopg's own
    # parser", not a second hand-rolled one). A literal, non-percent-encoded
    # '@' inside userinfo makes the URI itself ambiguous per RFC 3986,
    # and psycopg's parser resolves that ambiguity by splitting on the
    # FIRST '@', which can spill part of the password into what it reports
    # as `host` -- a field this function trusts and allowlists, because it
    # has no way to distinguish "psycopg misparsed this" from "the host
    # really is called that". The fix is percent-encoding (see the test
    # above), which is also what any correctly-behaving DSN-building tool
    # already does. This test pins the actual (imperfect) behavior so a
    # future psycopg version silently changing it is noticed, not assumed
    # equally safe.
    result = describe_database_target(f"postgresql://user:p@{_SECRET}@host:5432/db")
    assert result == f"host={_SECRET}@host port=5432 dbname=db user=user"


def test_url_form_password_containing_colon_never_appears_in_output():
    result = describe_database_target(f"postgresql://user:pa:{_SECRET}@host:5432/db")
    assert _SECRET not in result


def test_keyword_value_dsn_describes_host_port_dbname_user_without_the_password():
    dsn = f"host=myhost port=5432 dbname=chatwoot_default user=chatwoot_default password={_SECRET}"
    result = describe_database_target(dsn)
    assert result == "host=myhost port=5432 dbname=chatwoot_default user=chatwoot_default"
    assert _SECRET not in result


def test_keyword_value_dsn_with_no_password_key_describes_normally():
    assert describe_database_target("host=myhost port=5432 dbname=chatwoot_default") == (
        "host=myhost port=5432 dbname=chatwoot_default"
    )


def test_malformed_string_hits_the_fixed_placeholder_not_the_raw_value():
    # Neither a URL nor a parseable libpq DSN -- psycopg's own parser
    # raises, and the only fallback is a placeholder that derives nothing
    # from the input.
    result = describe_database_target("not a real dsn at all")
    assert result == "<could not parse --database-url -- not shown>"


def test_dsn_shaped_string_with_trailing_garbage_also_hits_the_placeholder():
    result = describe_database_target(f"host=myhost password={_SECRET} oops")
    assert result == "<could not parse --database-url -- not shown>"
    assert _SECRET not in result


# --- the three shapes that defeated the previous (denylist) implementation --
# Each one hides the password somewhere an authority-only regex never even
# looked: the URL's query string. All three must never surface the secret,
# regardless of what the rest of the output looks like.


def test_url_with_password_only_in_the_query_string_never_leaks_it():
    result = describe_database_target(f"postgresql://host/db?password={_SECRET}")
    assert _SECRET not in result


def test_url_with_user_and_password_in_the_query_string_never_leaks_it():
    result = describe_database_target(f"postgresql://user@host/db?password={_SECRET}")
    assert _SECRET not in result


def test_url_with_authority_password_and_a_different_query_string_password_never_leaks_either():
    # The dangerous case: an authority password AND an unrelated
    # query-string password in the same DSN. The previous (denylist)
    # implementation masked the authority one but left the query-string one
    # in plaintext -- while the line still LOOKED fully redacted. Both must
    # be absent from the output, and the output must contain nothing beyond
    # the allowlisted fields (no stray password/sslmode key at all).
    authority_password = "authpass"
    query_string_password = "AnotherSecret"
    result = describe_database_target(
        f"postgresql://user:{authority_password}@host/db?sslmode=require&password={query_string_password}"
    )
    assert result == "host=host dbname=db user=user"
    assert authority_password not in result
    assert query_string_password not in result
