from logisly_agents.config import get_settings
from logisly_agents.uc3.context import read_context


def test_context_loads():
    ctx = read_context(get_settings().data_dir / "uc3")
    assert {"prd_template.md", "user_story_template.md"} <= set(ctx["templates"])
    assert len(ctx["conventions"]["repos"]) == 5
    assert ctx["roster"]["people"]
    assert ctx["calendar"]["sprints"]
