Feature: Sync runs

  Scenario: An admin starts a run and opens its page
    Given I sign in as "web_admin"
    Then the header shows "web_admin (dp_admin)"
    When I open the "Sync" tab
    And I start a run for the schema "test"
    Then I see that a run started
    When I open the run that started
    Then I see the page of that run
    And I see the "Steps" table
    And the run waits in the queue, started by "web_admin"

  Scenario: The runs list pages through the runs
    Given I sign in as "web_admin"
    When I open the "Sync" tab
    Then the list with the column "Started by" has 25 rows
    And the list with the column "Started by" says "1 to 25 of"
    When I go to the "Older" rows of the list with the column "Started by"
    Then the list with the column "Started by" says "26 to"

  Scenario: The runs list filters by status
    Given I sign in as "web_admin"
    When I open the "Sync" tab
    And I set "Status" to "ERROR" in the form "Filter the runs" and submit it
    Then the list with the column "Started by" says "1 to 10 of 10"
    And every row of the list with the column "Started by" shows "ERROR"

  Scenario: The errors list pages and filters
    Given I sign in as "web_admin"
    When I open the "Sync" tab
    Then the list with the column "Reason" has 10 rows
    And the list with the column "Reason" says "1 to 10 of 15"
    When I set "Reason contains" to "seed error 1" in the form "Filter the errors" and submit it
    Then the list with the column "Reason" says "1 to 7 of 7"

  Scenario: An admin cancels a run and resumes it
    Given I sign in as "web_admin"
    When I open the "Sync" tab
    And I start a run for the schema "test"
    Then I see that a run started
    When I open the run that started
    And I cancel the run
    Then the run shows the status "CANCELLED"
    When I resume the run
    Then the run shows the status "ENQUEUED"

  Scenario: An admin runs a failed run again
    Given I sign in as "web_admin"
    When I open the "Sync" tab
    And I set "Status" to "ERROR" in the form "Filter the runs" and submit it
    And I open the first run of the list
    And I run the run again
    Then I see the page of that run
    And the run waits in the queue, started by "web_admin"

  Scenario: A viewer has no control buttons and the database refuses a direct call
    Given I sign in as "web_viewer"
    When I open the "Sync" tab
    And I open the first run of the list
    Then I do not see the "Cancel" button
    And I do not see the "Resume" button
    And I do not see the "Run again" button
    When I post a cancel request for this run directly
    Then the answer is 403

  Scenario: A user of another schema sees none of the runs
    Given I sign in as "web_other"
    When I open the "Sync" tab
    Then the list with the column "Started by" says "No rows"

  Scenario: An admin pauses and resumes the schedule
    Given I sign in as "web_admin"
    When I open the "Sync" tab
    And I pause the schedule "sync"
    Then the schedule "sync" shows the status "PAUSED"
    When I resume the schedule "sync"
    Then the schedule "sync" shows the status "ACTIVE"

  Scenario: An admin of one schema cannot pause the schedule of every schema
    Given I sign in as "web_other"
    When I post a pause request for the schedule "sync" directly
    Then the answer is 403

  Scenario: A finished run shows its tasks, steps and log
    Given I sign in as "web_admin"
    When I open the seeded run
    Then the run shows the status "SUCCESS"
    And the list with the column "Task" says "1 to 50 of 61"
    And the list with the column "Step" says "1 to 50 of 64"
    And the log shows 500 lines
    And the log says "Showing 500 of 600 lines."
    When I go to the "Older" rows of the list with the column "Task"
    Then the list with the column "Task" says "51 to 61 of 61"

  Scenario: The log loads more lines
    Given I sign in as "web_admin"
    When I open the seeded run
    And I load more log lines
    Then the log shows 600 lines
    And the log has no button to load more

  Scenario: A user of another schema cannot open the run
    Given I sign in as "web_other"
    When I open the seeded run
    Then I see the error "No such run"
