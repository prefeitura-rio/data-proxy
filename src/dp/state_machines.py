"""State machines for worker lifecycle and message claiming."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast, override

from statemachine import State, StateChart
from statemachine.transition_list import TransitionList


class BaseStateChart[T](StateChart[T]):
    """Provide the typed state-machine API used by the application."""

    @override
    def send(
        self,
        event: str,
        *args: object,
        delay: float = 0,
        send_id: str | None = None,
        internal: bool = False,
        **kwargs: object,
    ) -> object:
        """Dispatch an event without exposing the dependency's Any result type."""
        send = cast(Callable[..., object], super().send)
        return send(
            event,
            *args,
            delay=delay,
            send_id=send_id,
            internal=internal,
            **kwargs,
        )


@dataclass
class WorkerModel:
    """State data for a worker process."""

    state: str = "idle"


@dataclass
class ClaimModel:
    """State data for a one-shot message claim."""

    state: str = "unclaimed"


class WorkerState(BaseStateChart[WorkerModel]):
    """Track whether a subscriber failed so the worker Job reports the right exit code."""

    idle: State = State(initial=True, value="idle")
    failed: State = State(final=True, value="failed")
    fail: TransitionList = idle.to(failed)

    @property
    def exit_code(self) -> int:
        """Return the process exit code: 1 when failed, 0 otherwise."""
        return 1 if self.failed.is_active else 0


class ClaimState(BaseStateChart[ClaimModel]):
    """Let one pod process at most one message, for one-shot worker Jobs."""

    unclaimed: State = State(initial=True, value="unclaimed")
    claimed: State = State(final=True, value="claimed")
    claim: TransitionList = unclaimed.to(claimed)

    @property
    def is_claimed(self) -> bool:
        """Return whether the pod has already claimed a message."""
        return self.claimed.is_active


worker_state = WorkerState(model=WorkerModel())
publisher_claim = ClaimState(model=ClaimModel())
seeder_claim = ClaimState(model=ClaimModel())
