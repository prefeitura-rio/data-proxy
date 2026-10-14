"""Turn a value that changes over time into a stream of its changes."""

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable


async def changes[T](
    read: Callable[[], Awaitable[T]],
    *,
    interval: float,
    is_last: Callable[[T], bool] = lambda _: False,
) -> AsyncGenerator[T]:
    """Read a value every interval seconds and yield it when it is new.

    The first value is always yielded. The stream stops after the first value
    for which is_last is true.
    """
    first = True
    previous: T | None = None
    while True:
        value = await read()
        if first or value != previous:
            yield value
            first = False
            previous = value
        if is_last(value):
            return
        await asyncio.sleep(interval)
