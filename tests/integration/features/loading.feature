@postgres @s3
Feature: Parquet and S3 loading

  Background:
    Given a fresh PostgreSQL loading schema

  Scenario: Replacing and removing partitions updates the table incrementally
    When I incrementally replace partition 10 and remove partition 20
    Then partition 10 has the new data
    And partition 20 is removed
    And partition 30 is unchanged

  Scenario: Loading a missing Parquet path rolls back the table
    When I load a missing Parquet partition
    Then the table keeps its original rows

  Scenario: Creating a full table from S3 loads all partition rows
    When I create a full table from S3 Parquet
    Then the table contains all partition rows
    And the configured index exists on the table

  Scenario: Rebuilding a partitioned table from two S3 batches loads both
    When I rebuild a partitioned table from two S3 batches
    Then the table contains rows from both batches

  Scenario: Publishing an S3-backed full table loads the S3 data
    When I publish an S3-backed full table
    Then the published table contains the S3 data
