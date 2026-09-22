"""Tests for the async BigQuery facade."""

from unittest.mock import MagicMock, patch

import pytest
from google.cloud.bigquery import Client, QueryJobConfig, Table

from data_proxy.bigquery.clients import BigQuery


@pytest.mark.asyncio
async def test_connect_builds_and_closes_the_client() -> None:
    """
    GIVEN: a project identifier.
    WHEN: connect is entered and left.
    THEN: a client is built for that project and closed on exit.
    """
    client = MagicMock(spec=Client)

    with patch("data_proxy.bigquery.clients.Client", return_value=client) as build:
        async with BigQuery.connect("proj") as bigquery:
            assert bigquery.client is client

    build.assert_called_once_with(project="proj")
    client.close.assert_called_once()


@pytest.mark.asyncio
async def test_get_table_returns_metadata() -> None:
    """
    GIVEN: a client that resolves one table.
    WHEN: get_table runs.
    THEN: the table metadata is returned.
    """
    client = MagicMock(spec=Client)
    table = MagicMock(spec=Table)
    client.get_table.return_value = table

    result = await BigQuery(client=client).get_table("p.d.t")

    assert result is table
    client.get_table.assert_called_once_with("p.d.t")


@pytest.mark.asyncio
async def test_rows_materializes_the_job_result() -> None:
    """
    GIVEN: a client whose job yields two rows.
    WHEN: rows runs.
    THEN: every row is returned as a list.
    """
    client = MagicMock(spec=Client)
    job = MagicMock()
    job.result.return_value = [{"a": 1}, {"a": 2}]
    client.query.return_value = job

    result = await BigQuery(client=client).rows("SELECT 1", QueryJobConfig())

    assert list(result) == [{"a": 1}, {"a": 2}]
    client.query.assert_called_once()
