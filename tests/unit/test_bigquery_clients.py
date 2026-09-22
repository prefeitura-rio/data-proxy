"""Unit tests for the BigQuery client facade."""

from typing import cast
from unittest.mock import patch

import pytest
from google.cloud.bigquery import Client, QueryJobConfig, Table

from data_proxy.bigquery.clients import BigQuery
from tests.fixtures.unit import BigQueryClientDouble


class TestBigQueryClientLifecycle:
    """BigQueryClientLifecycle behavior tests."""

    @pytest.mark.asyncio
    async def test_closes_client_after_context_exit(
        self, bigquery_client_double: BigQueryClientDouble
    ) -> None:
        """Close the client when the context exits."""
        client = bigquery_client_double
        with patch(
            "data_proxy.bigquery.clients.Client",
            return_value=cast(Client, cast(object, client)),
        ) as build:
            async with BigQuery.connect("proj") as bigquery:
                assert bigquery.client is client
        assert build.call_args.kwargs == {"project": "proj"}
        assert client.closed


class TestBigQueryClientQueries:
    """BigQueryClientQueries behavior tests."""

    @pytest.mark.asyncio
    async def test_returns_table_metadata_from_client(
        self, bigquery_client_double: BigQueryClientDouble
    ) -> None:
        """Return table metadata from the client."""
        client = bigquery_client_double
        table = cast(Table, object())
        client.table = table
        result = await BigQuery(client=cast(Client, cast(object, client))).get_table(
            "p.d.t"
        )
        assert result is table
        assert client.requested_table == "p.d.t"

    @pytest.mark.asyncio
    async def test_materializes_query_job_rows(
        self, bigquery_client_double: BigQueryClientDouble
    ) -> None:
        """Materialize rows returned by a query job."""
        client = bigquery_client_double
        client.job.rows = cast(list[dict[str, object]], [{"a": 1}, {"a": 2}])
        result = await BigQuery(client=cast(Client, cast(object, client))).rows(
            "SELECT 1", QueryJobConfig()
        )
        assert list(result) == [{"a": 1}, {"a": 2}]
        assert client.query_text == "SELECT 1"
