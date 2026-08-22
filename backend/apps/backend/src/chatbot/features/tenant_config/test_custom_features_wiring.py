"""Integration smoke test: boot the full app and assert the route exists."""

from __future__ import annotations

from typing import Any

from chatbot.main import bootstrap_application


def _effective_routes(app: Any) -> Any:
    """Flatten `app.routes` into concrete routes with a real `.path`.

    FastAPI 0.137+ makes `app.include_router(...)` build a lazy
    `_IncludedRouter` wrapper rather than flat `APIRoute`/`Route` objects, so
    `{r.path for r in app.routes}` silently comes back without any of the
    ~20 routers this app includes -- not an `AttributeError`, just a route
    set that looks like the switchboard was never mounted.
    `_IncludedRouter.effective_candidates()` is the (undocumented, but only)
    way back to concrete routes; walk it recursively since a router can
    itself have been included into another router. Mirrors
    `test_p11_wiring.py::_effective_routes`.
    """

    def walk(routes: Any) -> Any:
        for r in routes:
            if hasattr(r, "effective_candidates"):
                yield from walk(r.effective_candidates())
            else:
                yield r

    yield from walk(app.routes)


def test_custom_features_route_is_mounted() -> None:
    """Reads must be mounted UNCONDITIONALLY, outside every feature-flag
    branch. The SPA calls this on every page load, and the composable fails
    closed — so a 404 here renders a blank CRM on a tenant that has features
    switched on."""
    app = bootstrap_application()
    paths = {r.path for r in _effective_routes(app) if hasattr(r, "path")}
    assert "/admin/custom-features" in paths


def test_custom_features_route_is_mounted_without_rbac() -> None:
    """The switchboard is a platform-level authority that exists whether or
    not a tenant opted into RBAC. If this route only appears inside the
    rbac_enabled branch, every tenant with RBAC off gets a blank CRM it
    cannot fix."""
    from chatbot.platform.config import get_settings

    assert get_settings().rbac_enabled is False  # the default this test relies on
    app = bootstrap_application()
    paths = {r.path for r in _effective_routes(app) if hasattr(r, "path")}
    assert "/admin/custom-features" in paths
