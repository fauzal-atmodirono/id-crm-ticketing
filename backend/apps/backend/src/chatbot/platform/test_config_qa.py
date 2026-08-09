import os

import pytest

from chatbot.platform.config import Settings


def test_qa_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # CALL_QA_ENABLED is exported by
    # deploy/scripts/check-suites-both-flag-states.sh's all-flags-ON run, and
    # `Settings(_env_file=None)` does NOT stop pydantic-settings reading
    # os.environ -- so without the delenv below this test asserted the opposite
    # of its own name on that run and failed there while passing locally. The
    # leak assertion is deliberate: deleting the delenv breaks the test rather
    # than quietly hollowing it out.
    monkeypatch.delenv("CALL_QA_ENABLED", raising=False)
    assert "CALL_QA_ENABLED" not in os.environ
    s = Settings()
    assert s.qa_provider == "noop"
    assert s.bigquery_qa_labels_table == "qa_labels"
    assert s.qa_api_key == ""
    assert s.call_qa_enabled is False
