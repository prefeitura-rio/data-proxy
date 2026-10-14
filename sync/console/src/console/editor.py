"""Read and write one resource of the config, guarded by the ETag of the resource."""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, final

from fastapi import BackgroundTasks, Depends, Header, Response
from fastapi.responses import JSONResponse

from console import config, proxies, revisions
from console.config import ApiDocument, is_object
from console.dependencies import Engine, ResourceWriting  # ruff: ignore[typing-only-first-party-import]
from console.models import Config, ConfigCheck
from console.problems import Problem
from console.settings import settings

if TYPE_CHECKING:
    from collections.abc import Callable

type Path = tuple[str, ...]


def etag_of(resource: object) -> str:
    """Name the content of a resource: the same content always has the same ETag."""
    text = json.dumps(resource, sort_keys=True, separators=(",", ":"))
    return f'"{sha256(text.encode()).hexdigest()[:16]}"'


def get_at(document: dict[str, object], path: Path) -> object | None:
    """Find the part of a document at a path, or None."""
    node: object = document
    for key in path:
        if not is_object(node) or key not in node:
            return None
        node = node[key]
    return node


def set_at(
    node: dict[str, object], path: Path, value: dict[str, object] | None
) -> dict[str, object]:
    """Return a copy of a document with the part at a path replaced, or removed when None."""
    key, *rest = path
    result = dict(node)
    child = result.get(key)
    if rest:
        result[key] = set_at(child if is_object(child) else {}, tuple(rest), value)
    elif value is None:
        result.pop(key, None)
    else:
        result[key] = value
    return result


@dataclass
class Condition:
    """What a write says about the resource: its ETag, or that it must not exist yet."""

    if_match: str | None
    if_none_match: str | None

    def verify(self, current: object | None) -> None:
        """Refuse a write that doesn't match the resource as it is now."""
        if self.if_none_match == "*":
            if current is not None:
                raise Problem(412, "The resource already exists")
        elif current is None or etag_of(current) != self.if_match:
            raise Problem(412, "The resource changed since you read it")


def condition(
    if_match: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Condition:
    """Read the precondition of a write: If-Match with an ETag, or If-None-Match: * to create."""
    if if_match is None and if_none_match is None:
        raise Problem(
            428,
            "Send If-Match with the ETag that you read, or If-None-Match: * to create",
        )
    return Condition(
        if_match=None
        if if_match is None
        else f'"{if_match.removeprefix("W/").strip(chr(34))}"',
        if_none_match=if_none_match,
    )


@final
class Editor:
    """Read and write one resource of the config for one request."""

    def __init__(
        self,
        engine: Engine,
        write: ResourceWriting,
        response: Response,
        background: BackgroundTasks,
    ) -> None:
        """Keep what the request carries."""
        self.engine = engine
        self.write = write
        self.response = response
        self.background = background

    async def active(self) -> dict[str, object]:
        """Read the active config in the form that the API shows."""
        async with self.engine.connect() as connection:
            active = await revisions.active(connection)
        return config.to_api(active.document) if active else {"schemas": {}}

    async def document(self) -> ApiDocument:
        """Read the active config as a typed document."""
        return ApiDocument.model_validate(await self.active())

    async def read(self, path: Path) -> dict[str, object]:
        """Read one resource, or answer 404."""
        resource = get_at(await self.active(), path)
        if not is_object(resource):
            raise Problem(404, "Not found")
        return resource

    async def show(self, path: Path) -> dict[str, object]:
        """Read one resource and put its ETag in the answer."""
        resource = await self.read(path)
        self.response.headers["ETag"] = etag_of(resource)
        return resource

    async def apply(
        self,
        path: Path,
        change: Callable[[dict[str, object] | None], dict[str, object] | None],
        *,
        must_exist: bool,
        guard: Condition,
    ) -> Config | ConfigCheck:
        """Change one resource in a new revision, after the guard accepts its current form."""

        def check(before: dict[str, object]) -> None:
            current = get_at(before, path)
            if must_exist and current is None:
                raise Problem(404, "Not found")
            guard.verify(current)

        def edit(before: dict[str, object]) -> dict[str, object]:
            if not is_object(get_at(before, path[:-1])):
                raise Problem(404, "The parent of the resource doesn't exist")
            current = get_at(before, path)
            return set_at(before, path, change(current if is_object(current) else None))

        return await config.change(self.engine, self.write, edit, check)

    def shown(
        self, result: Config | ConfigCheck, path: Path
    ) -> dict[str, object] | ConfigCheck:
        """Answer a write with the resource and its new ETag, and tell the proxies."""
        if isinstance(result, ConfigCheck):
            return result
        resource = get_at(result.document, path)
        if not is_object(resource) or result.revision is None:
            raise RuntimeError("The written resource is missing")
        self.background.add_task(proxies.notify, settings.PROXY_ADMIN_URL)
        self.response.headers["ETag"] = etag_of(resource)
        self.response.headers["X-Revision"] = str(result.revision.id)
        return resource

    async def replace(
        self, path: Path, body: dict[str, object], guard: Condition
    ) -> dict[str, object] | ConfigCheck:
        """Create the resource, or replace it."""
        result = await self.apply(path, lambda _: body, must_exist=False, guard=guard)
        return self.shown(result, path)

    async def patch(
        self, path: Path, body: dict[str, object], guard: Condition
    ) -> dict[str, object] | ConfigCheck:
        """Merge a JSON Merge Patch into the resource."""
        result = await self.apply(
            path,
            lambda current: config.merge_patch(current or {}, body),
            must_exist=True,
            guard=guard,
        )
        return self.shown(result, path)

    async def delete(self, path: Path, guard: Condition) -> Response:
        """Remove the resource from the config. The data that it describes stays."""
        result = await self.apply(path, lambda _: None, must_exist=True, guard=guard)
        if isinstance(result, ConfigCheck):
            return JSONResponse(result.model_dump())
        if result.revision is None:
            raise RuntimeError("The removal has no revision")
        self.background.add_task(proxies.notify, settings.PROXY_ADMIN_URL)
        return Response(
            status_code=204, headers={"X-Revision": str(result.revision.id)}
        )


Edit = Annotated[Editor, Depends()]
Guard = Annotated[Condition, Depends(condition)]
