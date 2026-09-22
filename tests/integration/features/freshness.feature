@postgres
Feature: Publication freshness

  Background:
    Given initialized freshness tables

  Scenario: Publishing a full-table freshness result replaces existing rows
    When I publish a full-table freshness result
    Then the full table has one successful freshness row

  Scenario: Publishing partition results records success and failure
    When I publish successful partition "1" and failed partition "2"
    Then partition freshness reports the expected statuses

  Scenario: Applying empty freshness batches changes no rows
    When I apply empty freshness batches
    Then no freshness rows are created

  Scenario: Recording explicit and derived failures stores both
    When I record explicit full-table and derived partition failures
    Then both freshness failures are stored

  Scenario: Publishing a full rebuild resets the partition manifest
    When I publish a full partition rebuild
    Then the current partition is marked successful

  Scenario: Recording explicit failure overrides takes precedence over derived failures
    When I record explicit failure overrides for the full table
    Then the explicit override partition is marked failed
