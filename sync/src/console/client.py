"""The part of the DBOS client that the API uses."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from dbos import StepInfo, WorkflowStatus


class WorkflowClient(Protocol):
    """The DBOS client calls of the API. Tests use a fake that implements them."""

    async def check_connection_async(self) -> None:
        """Raise when the DBOS system database is unreachable."""
        ...

    async def list_workflows_async(
        self,
        *,
        workflow_ids: list[str] | None = None,
        name: str | None = None,
        status: str | None = None,
        parent_workflow_id: str | None = None,
        start_time: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        sort_desc: bool = False,
        load_input: bool = True,
        load_output: bool = True,
    ) -> list[WorkflowStatus]:
        """List workflows, filtered by name, status and start time."""
        ...

    async def list_workflow_steps_async(
        self, workflow_id: str, *, load_output: bool = True
    ) -> list[StepInfo]:
        """List the steps of one workflow in the order that they ran."""
        ...
