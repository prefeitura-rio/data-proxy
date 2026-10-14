"""Validate a draft configuration with the model that the sync uses."""

from pydantic import BaseModel, ValidationError

from .models import SyncConfig


class Problem(BaseModel):
    """One problem of a draft: where it is, as a JSON Pointer, and what is wrong."""

    pointer: str
    message: str


def json_pointer(location: tuple[int | str, ...]) -> str:
    """Write a location in a document as a JSON Pointer."""
    return "".join(
        "/" + str(part).replace("~", "~0").replace("/", "~1") for part in location
    )


def check_config(document: str) -> list[Problem]:
    """Return every problem of a sync.json document, or nothing when it is valid."""
    try:
        SyncConfig.model_validate_json(document)
    except ValidationError as error:
        return [
            Problem(
                pointer=json_pointer(problem["loc"]),
                message=problem["msg"].removeprefix("Value error, "),
            )
            for problem in error.errors()
        ]

    return []
