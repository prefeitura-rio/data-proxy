"""Tests for DBOS application startup."""

import threading
from unittest.mock import patch

import dp.main as application
from dp.constants import DUMP_QUEUE, PUBLISH_QUEUE, SYNC_QUEUE


def test_main_registers_dbos_queues_and_schedule() -> None:
    with (
        patch.object(application, "DBOS") as dbos,
        patch.object(threading, "Event") as event,
    ):
        application.main()

    dbos.assert_called_once()
    dbos.listen_queues.assert_called_once_with([SYNC_QUEUE, DUMP_QUEUE, PUBLISH_QUEUE])
    dbos.launch.assert_called_once_with()
    assert dbos.register_queue.call_count == 3
    dbos.apply_schedules.assert_called_once()
    event.return_value.wait.assert_called_once_with()
