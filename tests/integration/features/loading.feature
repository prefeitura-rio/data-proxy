Feature: Parquet and Silo loading

  Scenario: Incremental partition replacement and removal
    Given a fresh PostgreSQL loading schema
    When I incrementally replace partition 10 and remove partition 20
    Then partition 10 has the new data
    And partition 20 is removed
    And partition 30 is unchanged

  Scenario: Rollback on missing Parquet path
    Given a fresh PostgreSQL loading schema
    When I load a missing Parquet partition
    Then the table keeps its original rows

  Scenario: Direct full-table creation from Silo
    Given a fresh PostgreSQL loading schema
    When I create a full table from Silo Parquet
    Then the table contains all partition rows
    And the configured index exists on the table

  Scenario: Full partition rebuild from multiple Silo batches
    Given a fresh PostgreSQL loading schema
    When I rebuild a partitioned table from two Silo batches
    Then the table contains rows from both batches

  Scenario: Complete Silo-backed publication
    Given a fresh PostgreSQL loading schema
    When I publish a Silo-backed full table
    Then the published table contains the Silo data
