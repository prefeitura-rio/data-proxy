Feature: Who manages the access policy

  Scenario: An admin adds, edits and removes an access policy
    Given I sign in as "web_admin"
    When I open the Access tab of the schema "default__test"
    And I add the policy "bdd-subject" for "cpf" "111"
    Then I see the policy "bdd-subject" with unit id "111"
    When I change the unit id of the policy "bdd-subject" to "222"
    Then I see the policy "bdd-subject" with unit id "222"
    And the access log lists "bdd-subject" with the action "insert"
    And the access log lists "bdd-subject" with the action "update"
    When I remove the policy "bdd-subject"
    Then I see no policy "bdd-subject"
    And the access log lists "bdd-subject" with the action "delete"

  Scenario: An admin adds several unit ids at once, with or without a space after the comma
    Given I sign in as "web_admin"
    When I open the Access tab of the schema "default__test"
    And I add the policy "bdd-many" for "region" with the unit ids "north,south, east"
    Then the toast says "Saved."
    And I see 3 policy rows for "bdd-many"
    And I see the policy "bdd-many" with unit id "north"
    And I see the policy "bdd-many" with unit id "south"
    And I see the policy "bdd-many" with unit id "east"
    When I add the policy "bdd-many" for "region" with the unit ids "north, west"
    Then the toast says "Saved 1, skipped 1 that already exist."
    And I see 4 policy rows for "bdd-many"
    When I remove the policy "bdd-many"
    And I remove the policy "bdd-many"
    And I remove the policy "bdd-many"
    And I remove the policy "bdd-many"
    Then I see no policy "bdd-many"

  Scenario: The form refuses a list that mixes the wildcard with ids
    Given I sign in as "web_admin"
    When I open the Access tab of the schema "default__test"
    And I add the policy "bdd-mixed" for "region" with the unit ids "north, *"
    Then the form says "Use * alone, not with other ids."
    And I see no policy "bdd-mixed"

  Scenario: Cancelling a delete keeps the policy
    Given I sign in as "web_admin"
    When I open the Access tab of the schema "default__test"
    And I set "Subject or unit id contains" to "seed-subject-07" in the form "Search the policy" and submit it
    And I start to remove the policy "seed-subject-07"
    And I cancel the dialog
    Then I see the policy "seed-subject-07"

  Scenario: A console user has no Access tab
    Given I sign in as "web_console"
    Then I do not see the "Access" tab

  Scenario: The policy list pages and searches
    Given I sign in as "web_admin"
    When I open the Access tab of the schema "default__test"
    Then the list with the column "Unit type" has 50 rows
    And the list with the column "Unit type" says "1 to 50 of"
    When I go to the "Older" rows of the list with the column "Unit type"
    Then the list with the column "Unit type" says "51 to"
    When I set "Subject or unit id contains" to "seed-subject-07" in the form "Search the policy" and submit it
    Then the list with the column "Unit type" has 1 rows

  Scenario: The access log filters by action
    Given I sign in as "web_admin"
    When I open the Access tab of the schema "default__test"
    And I set "Action" to "insert" in the form "Filter the access log" and submit it
    Then every row of the list with the column "Action" shows "insert"
    And the list with the column "Action" says "1 to 20 of"

  Scenario: A policy role sees the Access and Config tabs and no Sync or Query tab
    Given I sign in as "web_policy"
    Then I see the "Access" tab
    And I see the "Config" tab
    And I do not see the "Sync" tab
    And I do not see the "Query" tab

  Scenario: Explain shows a card that drops out when the subject has no grant of one type
    Given I sign in as "web_admin"
    When I open the Access tab of the schema "default__test"
    And I add the policy "bdd-explain" for "unit" "u1"
    And I open the "Config" tab
    And I add the table "p.d.explain" to the schema "default__test"
    And I build the row rules of "p.d.explain" with the keyboard: region_id of region and unit_id of unit, or area of area
    And I explain the subject "bdd-explain" in the editor
    Then the explanation says "The subject sees no row, because no condition has a grant."
    And the explanation lists "Group 1: drops out, no grant of region"
    And the explanation lists "unit_id (unit): u1"
    When I close the editor
    And I delete the table "p.d.explain"
    And I open the Access tab of the schema "default__test"
    And I remove the policy "bdd-explain"
    Then I see no policy "bdd-explain"

  Scenario: Explain counts a grant on the scope of one table
    Given I sign in as "web_admin"
    When I open the "Config" tab
    And I add the table "p.d.explain_scope" to the schema "default__test"
    And I add a row rule for the table "p.d.explain_scope" on column "area" with unit type "area"
    And I open the Access tab of the scope "default.test.explain_scope"
    And I add the policy "bdd-table" for "area" "a1"
    And I open the "Config" tab
    And I open the row rules of "p.d.explain_scope"
    And I explain the subject "bdd-table" in the editor
    Then the explanation says "The subject sees a row when its area is a1."
    When I close the editor
    And I delete the table "p.d.explain_scope"
    And I open the Access tab of the scope "default.test"
    Then I see no policy "bdd-table"

  Scenario: A policy role of one schema is offered the scopes of its schema and not the scope of every project
    Given I sign in as "web_policy"
    When I open the "Access" tab
    Then the scope list has "default.test"
    And the scope list has no "*"

  Scenario: An admin grants a policy for every project and removes it
    Given I sign in as "web_admin"
    When I open the Access tab of the scope "*"
    Then the scope list has "*"
    When I add the policy "bdd-everyone" for "cpf" "999"
    Then I see the policy "bdd-everyone" with unit id "999"
    When I remove the policy "bdd-everyone"
    Then I see no policy "bdd-everyone"
