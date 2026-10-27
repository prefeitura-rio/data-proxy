Feature: Scenarios that change data that other scenarios read

  Scenario: The exported document imports again
    Given I sign in as "web_admin"
    When I open the "Config" tab
    And I export the configuration
    Then the exported file lists the schema "test"
    When I preview the import of the exported file
    Then the preview says "Nothing changes"
    When I apply the previewed import
    Then I see the message "Saved."
    And I see the row of the schema "test"

  @import-ttl
  Scenario: An applied import updates a value and a second import restores it
    Given I sign in as "web_admin"
    When I open the "Config" tab
    And I export the configuration
    And I remember the exported file
    And I set the cache TTL of every table in the exported file to 322
    And I preview the import of the exported file
    And I apply the previewed import
    Then I see the message "Saved."
    And the table "rj-ia-desenvolvimento.dev.full_table" shows cache TTL "322"
    When I use the remembered file
    And I preview the import of the exported file
    And I apply the previewed import
    Then no table shows the cache TTL "322"

  @short-token @fresh-login
  Scenario: The console refreshes a token that is about to expire
    Given the identity provider issues tokens that last 60 seconds
    And I sign in as "web_admin"
    When I remember the session cookies
    And I reload the page
    Then I see the "Sync" tab
    And the session cookies are not the remembered ones

  Scenario: An admin of a schema pauses and resumes the schedule of the schema
    Given I sign in as "web_admin"
    When I open the "Sync" tab
    And I pause the schedule "sync-test"
    Then the schedule "sync-test" shows the status "PAUSED"
    When I resume the schedule "sync-test"
    Then the schedule "sync-test" shows the status "ACTIVE"

  Scenario: An admin of a schema changes the cron of its schedule
    Given I sign in as "web_admin"
    When I open the "Sync" tab
    And I change the cron of the schedule "sync-test" to "30 3 * * 1"
    Then the schedule "sync-test" shows the cron "30 3 * * 1"
    When I change the cron of the schedule "sync-test" to "0 2 * * *"
    Then the schedule "sync-test" shows the cron "0 2 * * *"

  # The Access page asks for the first schema of the user, and it answers 406 while a scenario adds or deletes one.
  # This file runs when no other scenario runs.
  Scenario: A role of the schema other opens the Access page directly
    Given I am signed in as "web_other"
    When I open the address "/access" directly
    Then the answer is 200
