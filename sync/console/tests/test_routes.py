import sys
from importlib import import_module
from pathlib import Path
from pkgutil import iter_modules
from types import ModuleType

import pytest
from fastapi import APIRouter
from fastapi.routing import iter_route_contexts

from console import routes
from console.main import app
from console.registry import discover_routers


def paths_of(routers: list[APIRouter]) -> list[str]:
    """List the effective paths that the routers declare, in order."""
    return [
        path
        for router in routers
        for context in iter_route_contexts(router.routes)
        if (path := context.path) is not None
    ]


def make_package(
    root: Path, monkeypatch: pytest.MonkeyPatch, name: str, modules: dict[str, str]
) -> ModuleType:
    """Write a package of modules under root, make it importable, and import it."""
    package = root / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    for module, source in modules.items():
        (package / f"{module}.py").write_text(source)
    monkeypatch.setattr(sys, "path", [str(root), *sys.path])
    return import_module(name)


def route_module(path: str) -> str:
    """Return the source of a route module with one route at the path."""
    return "\n".join(
        [
            "from fastapi import APIRouter",
            "router = APIRouter()",
            f"router.add_api_route('{path}', lambda: None)",
        ]
    )


class TestRoutes:
    """The registration of the route modules."""

    def test_finds_the_router_of_every_route_module(self) -> None:
        """Collect the routes of each public module in the routes package."""
        modules = [
            module.name
            for module in iter_modules(routes.__path__)
            if not module.name.startswith("_")
        ]

        assert len(discover_routers(routes)) == len(modules)

    def test_serves_everything_that_the_route_modules_declare(self) -> None:
        """Include every discovered router in the application."""
        declared = set(paths_of(discover_routers(routes)))

        assert declared
        assert set(app.openapi()["paths"]) == declared

    def test_registers_modules_in_name_order_and_skips_private_ones(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Import public modules by name, and leave the private ones alone."""
        package = make_package(
            tmp_path,
            monkeypatch,
            "fake_routes",
            {
                "b_second": route_module("/b"),
                "a_first": route_module("/a"),
                "_helper": "raise RuntimeError('never imported')",
            },
        )

        assert paths_of(discover_routers(package)) == ["/a", "/b"]

    def test_fails_for_a_public_module_without_a_router(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raise instead of skipping a route module that declares no router."""
        package = make_package(
            tmp_path, monkeypatch, "broken_routes", {"forgotten": "value = 1"}
        )

        with pytest.raises(
            TypeError, match=r"broken_routes\.forgotten must declare a router"
        ):
            discover_routers(package)
