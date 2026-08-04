"""`redact_database_url` is what stands between an operator's real tenant DB
password and a terminal/log: `backdate`'s dry-run summary prints it on
*every* invocation, not just `--execute`. Fail-closed by design: a string
that doesn't match one of the two shapes `psycopg.connect()` actually
accepts must never be echoed verbatim, because a summary that silently
prints an unredacted password is strictly worse than one that declines to
show the DSN at all."""

from __future__ import annotations

from backdate import redact_database_url


def test_url_form_redacts_the_password():
    assert redact_database_url(
        "postgresql://chatwoot_default:9f508f7afd4ba2bbcc59d72d4f58679e@postgres:5432/chatwoot_default"
    ) == "postgresql://chatwoot_default:***@postgres:5432/chatwoot_default"


def test_url_form_with_no_userinfo_is_returned_unchanged():
    # Nothing to redact -- there's no credential in the string at all.
    assert redact_database_url("postgresql://host:5432/db") == "postgresql://host:5432/db"


def test_url_form_with_user_but_no_password_is_returned_unchanged():
    assert redact_database_url("postgresql://user@host:5432/db") == "postgresql://user@host:5432/db"


def test_url_form_password_containing_at_sign_is_fully_masked():
    # Regression: matching the FIRST '@' instead of the LAST leaves
    # everything after the embedded '@' in plaintext.
    assert redact_database_url("postgresql://user:p@ssword@host:5432/db") == "postgresql://user:***@host:5432/db"


def test_url_form_password_containing_colon_is_fully_masked():
    # The user/password split is on the FIRST ':' in the userinfo, but the
    # whole remainder (including any further ':' characters) is masked as
    # one unit -- an internal colon in the password can't leak partial
    # content either side of it.
    assert redact_database_url("postgresql://user:pa:ss@host:5432/db") == "postgresql://user:***@host:5432/db"


def test_keyword_value_dsn_redacts_the_password():
    dsn = "host=myhost port=5432 dbname=chatwoot_default user=chatwoot_default password=SuperSecret123"
    assert redact_database_url(dsn) == (
        "host=myhost port=5432 dbname=chatwoot_default user=chatwoot_default password=***"
    )


def test_keyword_value_dsn_with_no_password_key_is_returned_unchanged():
    assert redact_database_url("host=myhost port=5432 dbname=chatwoot_default") == (
        "host=myhost port=5432 dbname=chatwoot_default"
    )


def test_unrecognised_string_hits_the_fixed_placeholder_not_the_raw_value():
    # Fail-closed: this is neither a scheme://... URL nor a string that
    # decomposes entirely into key=value tokens, so it must never be
    # echoed verbatim -- even though, in this particular case, it happens
    # not to contain a secret. The function can't tell that from the shape
    # alone, which is exactly the point.
    assert redact_database_url("not a real dsn at all") == "<unredactable DSN -- not shown>"


def test_a_dsn_shaped_string_with_trailing_garbage_also_hits_the_placeholder():
    # Looks almost like a kv-DSN but has a stray token with no '=' --
    # fail-closed means "almost recognised" is treated the same as
    # "not recognised", not partially trusted.
    assert redact_database_url("host=myhost password=SuperSecret123 oops") == "<unredactable DSN -- not shown>"
