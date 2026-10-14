"""Typed application state for the routes."""

from typing import TYPE_CHECKING, TypedDict

from fastapi import Request

if TYPE_CHECKING:
    from dbos import DBOSClient
    from sqlalchemy.ext.asyncio import AsyncEngine


class AppState(TypedDict):
    """The values that the lifespan opens at startup and shares with every request."""

    client: DBOSClient
    engine: AsyncEngine
    catalog: AsyncEngine | None


class AppRequest(Request[AppState]):
    """A request whose state is typed."""
