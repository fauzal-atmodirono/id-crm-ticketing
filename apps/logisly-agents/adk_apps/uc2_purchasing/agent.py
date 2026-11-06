"""ADK entry point for the UC2 purchasing assistant (`adk web adk_apps`)."""

from logisly_agents.config import configure_vertex_env

configure_vertex_env()

from logisly_agents.uc2.agent import root_agent  # noqa: E402

__all__ = ["root_agent"]
