"""Answer a failed request with an RFC 9457 problem document."""

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import Request

    from console.models import ErrorItem


class Problem(Exception):  # ruff: ignore[error-suffix-on-exception-name]
    """A request that the API refuses, with the status to answer and what is wrong."""

    status: int
    title: str
    errors: list[ErrorItem]

    def __init__(
        self, status: int, title: str, errors: list[ErrorItem] | None = None
    ) -> None:
        """Keep the status, the title and the problems."""
        super().__init__(title)
        self.status = status
        self.title = title
        self.errors = errors or []


def problem_handler(_: Request, error: Exception) -> JSONResponse:
    """Turn a Problem into a problem+json answer."""
    if not isinstance(error, Problem):
        raise error
    return JSONResponse(
        status_code=error.status,
        media_type="application/problem+json",
        content={
            "title": error.title,
            "status": error.status,
            "errors": [item.model_dump() for item in error.errors],
        },
    )
