"""Answer a failed request with an RFC 9457 problem document."""

from typing import TYPE_CHECKING

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from console.models import ErrorItem

if TYPE_CHECKING:
    from fastapi import Request
    from pydantic_core import ErrorDetails


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


def validation_handler(request: Request, error: Exception) -> JSONResponse:
    """Turn a request that FastAPI refuses into a problem+json answer, with JSON Pointers."""
    if not isinstance(error, RequestValidationError):
        raise error
    details: list[ErrorDetails] = list(error.errors())
    errors = [
        ErrorItem(
            pointer="".join(f"/{part}" for part in item["loc"][1:]), message=item["msg"]
        )
        for item in details
    ]
    return problem_handler(request, Problem(422, "The request isn't valid", errors))


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
