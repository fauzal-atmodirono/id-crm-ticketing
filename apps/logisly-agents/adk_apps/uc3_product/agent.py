"""ADK entry point for the UC3 product manager assistant (`adk web adk_apps`)."""

from logisly_agents.config import configure_vertex_env

configure_vertex_env()

from logisly_agents.uc3.agent import root_agent  # noqa: E402

__all__ = ["root_agent"]
