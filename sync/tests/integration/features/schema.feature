@postgres
Feature: PostgreSQL schema lifecycle

  Background:
    Given a fresh PostgreSQL schema

  Scenario: Initializing two schemas installs procedures and creates both schemas
    When I initialize two configured application schemas
    Then the maintenance procedures are installed
    And both configured schemas exist

  Scenario: Initializing one schema preserves authenticator memberships
    When I initialize one configured application schema
    Then authenticator memberships are unchanged

  Scenario: Revoking anonymous access disables schema and table privileges
    When I revoke anonymous access for the configured schema
    Then anonymous schema usage is disabled
    And anonymous table access is disabled

  Scenario: Cleaning schema objects removes stale views
    When I create a stale view and clean schema objects
    Then the stale view does not exist

  Scenario: Initializing the application state schema creates state and errors tables
    When I initialize the application state schema
    Then the state and errors tables exist
