from app.services import lifecycle_store


async def test_seed_then_get_state():
    assert await lifecycle_store.get_state(101) is None
    await lifecycle_store.seed_active(101, channel="Channel::Whatsapp")
    assert await lifecycle_store.get_state(101) == "active"


async def test_seed_is_idempotent_and_does_not_clobber():
    await lifecycle_store.seed_active(102, channel="Channel::Api")
    await lifecycle_store.transition(102, "idle_warned")
    # Seeding again must NOT reset an existing row back to active.
    await lifecycle_store.seed_active(102, channel="Channel::Api")
    assert await lifecycle_store.get_state(102) == "idle_warned"


async def test_transition_updates_state_and_fields():
    await lifecycle_store.seed_active(103, channel="Channel::Api")
    await lifecycle_store.transition(103, "awaiting_survey", survey_variant="ai")
    row = await lifecycle_store.get_row(103)
    assert row.state == "awaiting_survey"
    assert row.survey_variant == "ai"


async def test_transition_on_missing_row_creates_it():
    await lifecycle_store.transition(104, "closed")
    assert await lifecycle_store.get_state(104) == "closed"
