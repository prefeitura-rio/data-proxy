Feature: PostgreSQL schema lifecycle

  Scenario: Initialization installs application procedures and schemas
    Given a fresh PostgreSQL schema
    When I initialize two configured application schemas
    Then the maintenance procedures are installed
    And both configured schemas exist

  Scenario: Initialization does not change authenticator memberships
    Given a fresh PostgreSQL schema
    When I initialize one configured application schema
    Then authenticator memberships are unchanged

  Scenario: Anonymous access is revoked
    Given a fresh PostgreSQL schema
    When I revoke anonymous access for the configured schema
    Then anonymous schema usage is disabled
    And anonymous table access is disabled

  Scenario: Stale tables are removed
    Given a fresh PostgreSQL schema
    When I create a stale table and clean schema objects
    Then the stale table does not exist
