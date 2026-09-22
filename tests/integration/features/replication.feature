@postgres
Feature: PostgreSQL replication readiness

  Background:
    Given a fresh PostgreSQL replication schema

  Scenario: Reading the current WAL position returns a non-empty LSN
    When I read the current WAL position
    Then a non-empty WAL LSN is returned

  Scenario: Checking replica replay for a future LSN completes without standbys
    When I check replica replay for a future LSN
    Then the replica replay check completes
