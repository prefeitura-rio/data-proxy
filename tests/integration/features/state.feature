@postgres
Feature: Persisted synchronization state

  Background:
    Given an empty application state database

  Scenario: Reading a written full-table state returns the signature
    When I write full-table state for "p.d.t" with signature "abc"
    Then reading table "p.d.t" returns signature "abc"

  Scenario: Replacing table state overwrites the old signature
    When I replace table "p.d.t" signature "old" with "new"
    Then reading table "p.d.t" returns signature "new"

  Scenario: Reading an unknown table returns no state
    Then reading table "p.d.unknown" has no state

  Scenario: Recording an extraction error persists the event
    When I record an extraction error for "p.d.t"
    Then the latest error for "p.d.t" has reason "extraction_failed"

  Scenario: Cleaning unconfigured state removes stale table state
    When I write stale state for "p.d.stale" and clean unconfigured state
    Then reading table "p.d.stale" has no state

  Scenario: Writing partitioned state round-trips through the manifest
    When I write partitioned state for "p.d.t" with one partition
    Then reading the partition manifest for "p.d.t" returns the partition

  Scenario: Building table states preserves unpublished table state
    When I write state for "p.d.published" and "p.d.unpublished"
    And I build table states for only "p.d.published"
    Then state for "p.d.unpublished" keeps its original signature
