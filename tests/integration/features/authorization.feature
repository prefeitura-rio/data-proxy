@postgres
Feature: PostgreSQL authorization

  Scenario: Applying authorization to an unprotected table grants schema-scoped access
    Given a fresh PostgreSQL authorization schema
    When I apply authorization to an unprotected table
    Then the table has the schema-scoped policy
    And the user role has select access

  Scenario: Applying authorization to a protected table installs the access-policy check
    Given a fresh PostgreSQL authorization schema
    When I apply authorization to a protected table with an identity claim
    Then the table has the access-policy policy

  Scenario: Deleting an access-policy grant revokes row visibility
    Given a production access-policy schema
    When I set up a protected visible table with an allowed grant for "alice"
    And I query the protected table as "alice"
    Then the protected table shows the allowed row
    When I delete the access-policy grant for "alice"
    And I query the protected table as "alice"
    Then the protected table shows no rows

  Scenario: Claiming a non-matching schema hides scoped rows
    Given a fresh PostgreSQL authorization schema
    When I apply authorization to a schema-scoped table
    And I query the scoped table with the matching schema claim
    Then the scoped table shows the visible row
    When I query the scoped table with a non-matching schema claim
    Then the scoped table shows no visible rows

  Scenario: Inspecting the access-log trigger confirms security definer
    Given a production access-policy schema
    Then the access-log trigger is a security definer

  Scenario: Inserting an access-policy grant logs the insert
    Given a production access-policy schema
    When I insert an access-policy grant for "123"
    Then the access-log records an insert for "123"

  Scenario: Updating an access-policy grant logs the old state
    Given a production access-policy schema
    When I insert and update the access-policy grant for "456"
    Then the access-log records an insert and update for "456"

  Scenario: Deleting an access-policy grant logs the old state
    Given a production access-policy schema
    When I insert and delete the access-policy grant for "789"
    Then the access-log records an insert and delete for "789"

  Scenario: Pruning the access log removes entries outside the retention window
    Given a production access-policy schema
    When I insert a recent access-policy grant for "recent"
    And I insert a stale access-log entry for "stale"
    And I prune the access log with a 90-day retention
    Then only the recent access-log entry remains
