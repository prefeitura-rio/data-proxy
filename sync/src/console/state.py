"""Typed application state for the routes."""

from typing import TYPE_CHECKING, TypedDict

from fastapi import Request

if TYPE_CHECKING:
    from console.client import WorkflowClient


class AppState(TypedDict):
    """The values that the lifespan opens at startup and shares with every request."""

    client: WorkflowClient


class AppRequest(Request[AppState]):
    """A request whose state is typed."""
