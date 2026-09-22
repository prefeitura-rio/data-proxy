@postgres
Feature: PostgreSQL table publication

  Background:
    Given a fresh PostgreSQL publication schema

  Scenario: Converting JSON columns produces JSONB columns
    When I convert the JSON columns of a table to JSONB
    Then the table has JSONB columns

  Scenario: Publishing a shadow table replaces the live table
    When I publish a prepared shadow table
    Then the live table contains the shadow rows
    And the configured index exists on the live table

  Scenario: Publishing a table with a gin expression index creates the index
    When I publish a table with a gin expression index
    Then the expression index exists on the live table

  Scenario: Preparing an incremental plan for a missing table fails
    When I prepare an incremental plan for a missing table
    Then preparation fails with a missing table error

  Scenario: Preparing tables with an empty changed set prepares nothing
    When I prepare tables with an empty changed set
    Then no tables are prepared

  Scenario: Preparing a table with a missing Parquet path prepares nothing
    When I prepare a table with a missing Parquet path
    Then no tables are prepared

  Scenario: Publishing a batch with one failing table excludes the failed table
    When I publish a batch with one failing table
    Then only the successful table is published
