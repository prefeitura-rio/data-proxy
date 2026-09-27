@postgres
Feature: SQL template execution against PostgreSQL

  Background:
    Given a fresh PostgreSQL schema for templates

  Scenario: The init_schema template creates state and errors tables
    When I execute the init_schema template
    Then the state table exists from the template
    And the errors table exists from the template

  Scenario: The access_policy_check template enables row-level security
    Given a fresh PostgreSQL authorization schema for templates
    When I apply authorization to an unprotected table through templates
    Then the table has the schema-scoped policy from the template
    And the user role has select access from the template

  Scenario: The access_policy_check template creates a scoped policy for protected tables
    Given a fresh PostgreSQL authorization schema for templates
    When I apply authorization to a protected table through templates
    Then the table has the access-policy policy from the template

  Scenario: Reconciling DuckLake views creates the view, function, change-feed function, and snapshot function
    Given a fresh PostgreSQL schema for templates
    When I reconcile DuckLake views for one table
    Then the DuckLake view exists
    And the DuckLake query function exists
    And the change-feed function exists
    And the snapshot function exists

  Scenario: Reconciling DuckLake views with a changed config drops stale views and functions
    Given a fresh PostgreSQL schema for templates
    When I reconcile DuckLake views for one table
    And I reconcile DuckLake views with an empty config
    Then the DuckLake view does not exist
    And the DuckLake query function does not exist

  Scenario: Clearing an S3 prefix removes objects under it
    Given a fresh PostgreSQL schema for templates
    When I upload objects to S3 under a test prefix
    And I clear the S3 prefix
    Then the S3 objects are gone
