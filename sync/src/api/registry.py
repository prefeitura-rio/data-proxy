"""Find the routers of a package of route modules."""

from importlib import import_module
from inspect import getmembers
from pkgutil import iter_modules
from typing import TYPE_CHECKING, TypeIs

from fastapi import APIRouter

if TYPE_CHECKING:
    from types import ModuleType


def is_router(value: object) -> TypeIs[APIRouter]:
    """Tell a type checker that a value is a router."""
    return isinstance(value, APIRouter)


def router_of(module: ModuleType) -> APIRouter:
    """Return the router that a route module declares as ``router``.

    Raise ``TypeError`` if the module declares none.
    """
    routers = [
        router for name, router in getmembers(module, is_router) if name == "router"
    ]

    if not routers:
        raise TypeError(f"{module.__name__} must declare a router")

    return routers[0]


def discover_routers(package: ModuleType) -> list[APIRouter]:
    """Import every public module of a package and collect its router, by module name.

    A module whose name starts with an underscore is not a route module and is skipped.
    A public module without a router is a mistake, so the function raises.
    """
    names = sorted(
        module.name
        for module in iter_modules(package.__path__)
        if not module.name.startswith("_")
    )

    return [router_of(import_module(f"{package.__name__}.{name}")) for name in names]
