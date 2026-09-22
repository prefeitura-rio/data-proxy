Feature: PostgreSQL table publication

  Scenario: JSON columns are converted to JSONB before publication
    Given a fresh PostgreSQL publication schema
    When I convert the JSON columns of a table to JSONB
    Then the table has JSONB columns

  Scenario: A shadow table replaces the live table
    Given a fresh PostgreSQL publication schema
    When I publish a prepared shadow table
    Then the live table contains the shadow rows
    And the configured index exists on the live table
