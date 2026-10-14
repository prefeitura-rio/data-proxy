"""Find the routers of a package of route modules."""

from importlib import import_module
from operator import attrgetter
from pkgutil import iter_modules
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from types import ModuleType


def discover_routers(package: ModuleType) -> list[APIRouter]:
    """Import every public module of a package and collect its ``router``, by module name.

    A module whose name starts with an underscore is not a route module and is skipped.
    A public module that declares no ``router`` is a mistake, so the function raises
    ``TypeError``.
    """
    routers: list[APIRouter] = []

    for info in sorted(iter_modules(package.__path__), key=attrgetter("name")):
        if info.name.startswith("_"):
            continue

        module = import_module(f"{package.__name__}.{info.name}")

        match getattr(module, "router", None):
            case APIRouter() as router:
                routers.append(router)
            case _:
                raise TypeError(f"{module.__name__} must declare a router")

    return routers
