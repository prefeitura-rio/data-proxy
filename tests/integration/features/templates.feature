@postgres
Feature: SQL template execution against PostgreSQL

  Background:
    Given a fresh PostgreSQL schema for templates

  Scenario: Reconciling DuckLake views creates the view and every managed function
    When I reconcile DuckLake views for one table
    Then the view "people" exists
    And the function "people_fn()" exists
    And the function "people_dl_fn(text, text)" exists
    And the function "ducklake_changes_people(bigint, bigint)" exists
    And the function "ducklake_latest_snapshot()" exists
    And the application function "plan_sources(text, text[], boolean)" exists
    And the function "people_bq_fn(text, text)" does not exist

  Scenario: Reconciling a table with a BigQuery fallback creates the BigQuery helper
    When I reconcile DuckLake views for one table with the "bigquery" fallback
    Then the function "people_bq_fn(text, text)" exists

  Scenario: Reconciling after a source column change replaces the view columns
    When I reconcile DuckLake views for one table with the columns "cpf BIGINT, name VARCHAR"
    And I reconcile DuckLake views for one table with the columns "cpf BIGINT, name VARCHAR, active BOOLEAN"
    Then the view "people" has the columns "cpf, name, active"

  Scenario: Reconciling DuckLake views with a changed config drops stale views and functions
    When I reconcile DuckLake views for one table
    And I reconcile DuckLake views with an empty config
    Then the view "people" does not exist
    And the function "people_fn()" does not exist
    And the function "people_dl_fn(text, text)" does not exist
    And the function "ducklake_changes_people(bigint, bigint)" does not exist

  Scenario: Clearing an S3 prefix removes objects under it
    Given a fresh PostgreSQL schema for templates
    When I upload objects to S3 under a test prefix
    And I clear the S3 prefix
    Then the S3 objects are gone
