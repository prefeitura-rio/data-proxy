Feature: publication freshness

  Scenario: Full-table freshness is replaced after publication
    Given initialized freshness tables
    When I publish a full-table freshness result
    Then the full table has one successful freshness row

  Scenario: Partition freshness records success and failure
    Given initialized freshness tables
    When I publish successful partition "1" and failed partition "2"
    Then partition freshness reports the expected statuses

  Scenario: Empty freshness batches do not change state
    Given initialized freshness tables
    When I apply empty freshness batches
    Then no freshness rows are created

  Scenario: Explicit and derived freshness failures are recorded
    Given initialized freshness tables
    When I record explicit full-table and derived partition failures
    Then both freshness failures are stored

  Scenario: Full rebuild resets the partition manifest
    Given initialized freshness tables
    When I publish a full partition rebuild
    Then the current partition is marked successful
