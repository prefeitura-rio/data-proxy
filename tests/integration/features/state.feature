Feature: Persisted synchronization state

  Scenario: A written full-table state can be read back
    Given an empty application state database
    When I write full-table state for "p.d.t" with signature "abc"
    Then reading table "p.d.t" returns signature "abc"

  Scenario: A newer table state replaces the old signature
    Given an empty application state database
    When I replace table "p.d.t" signature "old" with "new"
    Then reading table "p.d.t" returns signature "new"

  Scenario: An unknown table has no state
    Given an empty application state database
    Then reading table "p.d.unknown" has no state

  Scenario: An error event is persisted
    Given an empty application state database
    When I record an extraction error for "p.d.t"
    Then the latest error for "p.d.t" has reason "extraction_failed"

  Scenario: Stale table state is removed
    Given an empty application state database
    When I write stale state for "p.d.stale" and clean unconfigured state
    Then reading table "p.d.stale" has no state
