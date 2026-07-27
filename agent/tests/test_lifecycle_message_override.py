from app.services import lifecycle


def test_per_inbox_wins():
    t = {"idle_close_message": "Custom close"}
    assert lifecycle._resolve_lifecycle_message(t, {"idle_close": "persona"}, "idle_close", "def") == "Custom close"


def test_falls_back_to_persona():
    assert lifecycle._resolve_lifecycle_message({}, {"idle_close": "persona"}, "idle_close", "def") == "persona"


def test_falls_back_to_default():
    assert lifecycle._resolve_lifecycle_message(None, None, "idle_close", "def") == "def"


def test_blank_per_inbox_ignored():
    t = {"idle_close_message": "   "}
    assert lifecycle._resolve_lifecycle_message(t, {"idle_close": "persona"}, "idle_close", "def") == "persona"
