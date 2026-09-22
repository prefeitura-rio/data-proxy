@postgres
Feature: PostgreSQL connection lifecycle

  Scenario: Opening a PostgreSQL connection yields a usable connection
    When I open a PostgreSQL connection
    Then the connection is usable
