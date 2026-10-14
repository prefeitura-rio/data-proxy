"""Find the routers of a package of route modules."""

from importlib import import_module
from pkgutil import iter_modules
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from types import ModuleType

    from fastapi import APIRouter


def discover_routers(package: ModuleType) -> list[APIRouter]:
    """Import every public module of a package and collect its ``router``, by module name.

    A module whose name starts with an underscore is not a route module and is skipped.
    A public module without a ``router`` is a mistake, so the import raises.
    """
    names = sorted(
        module.name
        for module in iter_modules(package.__path__)
        if not module.name.startswith("_")
    )
    return [
        cast("APIRouter", import_module(f"{package.__name__}.{name}").router)
        for name in names
    ]
