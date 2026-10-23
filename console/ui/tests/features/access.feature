Feature: Who sees and does what

  Scenario: A console user has no Start button and gets 403 on a direct call
    Given I sign in as "web_viewer"
    Then I see the "Sync" tab
    And I do not see the "Query" tab
    And I do not see the "Start run" button
    When I post a run request directly
    Then the answer is 403

  Scenario: A user with another schemas claim sees no rows of the schema
    Given I sign in as "web_other"
    When I open the "Config" tab
    Then I see no row of the schema "test"

  Scenario: The health check needs no session
    Then the health check answers without a session

  Scenario: An expired session returns to sign-in
    Given I sign in as "web_admin"
    When the session cookie is damaged and the identity provider has no session
    And I reload the page
    Then I am on the sign-in page

  Scenario: An admin adds, edits and removes an access policy
    Given I sign in as "web_admin"
    When I open the "Access" tab
    And I add the policy "bdd-subject" for "cpf" "111"
    Then I see the policy "bdd-subject" with unit id "111"
    When I change the unit id of the policy "bdd-subject" to "222"
    Then I see the policy "bdd-subject" with unit id "222"
    And the access log lists "bdd-subject" with the action "insert"
    And the access log lists "bdd-subject" with the action "update"
    When I remove the policy "bdd-subject"
    Then I see no policy "bdd-subject"
    And the access log lists "bdd-subject" with the action "delete"

  Scenario: A console user has no Access tab
    Given I sign in as "web_viewer"
    Then I do not see the "Access" tab

  Scenario: The policy list pages and searches
    Given I sign in as "web_admin"
    When I open the "Access" tab
    Then the list with the column "Unit type" has 50 rows
    And the list with the column "Unit type" says "1 to 50 of"
    When I go to the "Older" rows of the list with the column "Unit type"
    Then the list with the column "Unit type" says "51 to"
    When I set "Subject or unit id contains" to "seed-subject-07" in the form "Search the policy" and submit it
    Then the list with the column "Unit type" has 1 rows

  Scenario: The access log filters by action
    Given I sign in as "web_admin"
    When I open the "Access" tab
    And I set "Action" to "insert" in the form "Filter the access log" and submit it
    Then every row of the list with the column "Action" shows "insert"
    And the list with the column "Action" says "1 to 20 of"

  Scenario: A user signs out
    Given I sign in as "web_admin"
    When I sign out
    Then I am on the sign-in page
    When I open the seeded run
    Then I am on the sign-in page

  Scenario: A role with no console permission sees the no-access page
    Given I sign in as "web_api"
    Then I am on the no-access page
    And I do not see the "Sync" tab

  Scenario: A token without a role sees the no-access page and does not loop
    Given I sign in as "web_norole"
    Then I am on the no-access page
    And I do not see the "Sync" tab

  Scenario: A policy role sees only the Access tab
    Given I sign in as "web_policy"
    Then I see the "Access" tab
    And I do not see the "Sync" tab
    And I do not see the "Config" tab
    And I do not see the "Query" tab

  Scenario: The access log pages
    Given I sign in as "web_admin"
    When I open the "Access" tab
    Then the list with the column "Action" says "1 to 20 of"
    When I go to the "Older" rows of the list with the column "Action"
    Then the list with the column "Action" says "21 to 40 of"
