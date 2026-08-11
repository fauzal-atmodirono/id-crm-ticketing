"""P11 Task 5 -- what `handoff_target.py` actually resolves, named for it.

**Task 5 is not done, and this file no longer says it is.** Three of its tests
were named for behaviour that does not exist in `handoff_target.py`:

- `test_rsa_and_non_rsa_resolve_to_different_targets` resolved **one** target
  and asserted it was the single env-configured number. The RFP requirement it
  claimed to verify is that an RSA (roadside) call and a non-RSA call reach
  different teams; there is no RSA branch anywhere in the resolver. A green test
  under that name is worse than no test: it reports MET a property whose failure
  mode is a stranded motorist at 2 a.m. and a billing enquiry both dialling the
  same hunt group while the on-call RSA rota receives neither.
- `test_targets_are_read_from_the_admin_store_not_from_env` read
  `settings.phone_handoff_target_number` and asserted that env value -- the exact
  opposite of its own name. There is no admin store for handoff targets.
- `test_agent_selection_reuses_pick_agent_and_not_a_second_implementation` was a
  `hasattr(RoutingAssigner, "assign")`. Nothing in `handoff_target.py` imports or
  calls the routing feature at all.

They are renamed here to state what they verify, rather than deleted, because the
current single-static-target behaviour is real and worth locking down. Each
carries a tripwire that fails the day the missing feature lands, so the name has
to be revisited then instead of quietly becoming true-by-accident again. See the
review at `.superpowers/sdd/2026-08-08-rfp-p10-admin-and-access-control/
review-p10-p11-p12.md` (finding C-2) and the P11 ledger.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from chatbot.features.chat.phone.handoff_target import (
    HandoffTarget,
    HandoffTargetResolver,
    validate_handoff_target_settings,
)


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(
        update={
            "phone_handoff_enabled": True,
            "phone_handoff_target_number": "+60388889999",
            "phone_handoff_caller_id": "+60311112222",
        }
    )


@pytest.fixture
def mock_log_port():
    port = AsyncMock()
    port.get_inbox_working_hours.return_value = None
    return port


async def test_every_call_resolves_to_the_same_single_static_target_rsa_or_not(
    settings, mock_log_port
) -> None:
    """The RSA-vs-non-RSA split is NOT implemented; this is what is.

    The signature assertion is the load-bearing half: `resolve()` accepts no
    call context whatsoever, so there is nothing an RSA call could carry that
    would let it resolve differently. That is a stronger statement than "the
    number happens to be the env one", and it is what fails when somebody
    finally adds an RSA branch -- at which point this test's name is wrong
    again and must be changed back.
    """
    resolver = HandoffTargetResolver(settings, mock_log_port)
    assert list(inspect.signature(resolver.resolve).parameters) == []

    target = await resolver.resolve()
    assert target is not None
    assert target.kind == "pstn"
    assert target.value == "+60388889999"


async def test_the_target_is_read_from_the_env_setting_not_from_an_admin_store(
    settings, mock_log_port
) -> None:
    """Proves the direction of the dependency by changing the input.

    Asserting the env value against a resolver built from that same env value
    (what the old test did) is consistent with the number coming from anywhere.
    Two resolvers over two different settings objects, returning two different
    numbers, can only mean the setting is the source -- and `mock_log_port` is
    the resolver's only other collaborator, consulted solely for business hours.
    """
    first = HandoffTargetResolver(settings, mock_log_port)
    second = HandoffTargetResolver(
        settings.model_copy(update={"phone_handoff_target_number": "+60377776666"}),
        mock_log_port,
    )

    first_target = await first.resolve()
    second_target = await second.resolve()
    assert first_target is not None
    assert second_target is not None
    assert first_target.value == "+60388889999"
    assert second_target.value == "+60377776666"


async def test_a_client_kind_target_carries_the_agent_identity_as_its_value() -> None:
    target = HandoffTarget(kind="client", value="agent_7_identity")
    assert target.kind == "client"
    assert target.value == "agent_7_identity"


def test_the_resolver_does_not_consult_the_routing_feature_at_all() -> None:
    """Renamed from `..._reuses_pick_agent_and_not_a_second_implementation`.

    Per-agent target selection (design doc §5.2) is unbuilt, so there is no
    reuse to assert. What *can* be asserted is the negative the old name was
    reaching for: this module contains no agent-selection code, neither
    borrowed nor duplicated. Reading the module source is the honest form --
    the old `hasattr(RoutingAssigner, "assign")` passed on the strength of a
    class this module never mentions.
    """
    from chatbot.features.chat.phone import handoff_target

    source = inspect.getsource(handoff_target)
    assert "features.routing" not in source
    assert "pick_agent" not in source


def test_the_service_refuses_to_start_with_a_placeholder_number_configured(settings) -> None:
    invalid_settings = settings.model_copy(
        update={"phone_handoff_target_number": "+60300000001"}
    )
    with pytest.raises(ValueError, match="configured with placeholder number"):
        validate_handoff_target_settings(invalid_settings)


def test_the_startup_error_names_the_offending_setting(settings) -> None:
    invalid_settings = settings.model_copy(
        update={"phone_handoff_target_number": "+60300000001"}
    )
    with pytest.raises(ValueError, match="phone_handoff_target_number"):
        validate_handoff_target_settings(invalid_settings)


def test_an_unconfigured_rsa_target_is_a_startup_error_not_a_runtime_surprise(settings) -> None:
    invalid_settings = settings.model_copy(
        update={"phone_handoff_target_number": "+60000000000"}
    )
    with pytest.raises(ValueError, match="placeholder number"):
        validate_handoff_target_settings(invalid_settings)
