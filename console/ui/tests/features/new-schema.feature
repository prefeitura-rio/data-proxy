@new-schema
Feature: Scenarios that add, change and delete a schema of their own

  Scenario: Schema delete needs the typed name
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_delete"
    And I ask to delete the schema "default__other_delete"
    Then the confirm button stays disabled until I type "default__other_delete"
    When I confirm the deletion
    Then I see no row of the schema "default__other_delete"

  Scenario: A schema, a table and a row rule go through their whole life
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_life"
    And I edit the schema "default__other_life" with claim "cpf"
    And I add the table "p.d.life" to the schema "default__other_life"
    And I edit the table "p.d.life" with cache TTL "120"
    Then the table "p.d.life" shows cache TTL "120"
    When I add a row rule for the table "p.d.life" on column "id" with unit type "cpf"
    And I change the unit type of the rule on "p.d.life" to "cnpj"
    Then I see a row rule on "p.d.life" with unit type "cnpj"
    When I delete the row rule of "p.d.life"
    Then I see no row rule on "p.d.life"
    When I delete the table "p.d.life"
    Then I see no table "p.d.life"
    When I delete the schema "default__other_life" by typing its name
    Then I see no row of the schema "default__other_life"

  Scenario: A change shows in the history with the user name
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_history"
    And I edit the schema "default__other_history" with claim "cpf"
    Then the history lists "insert" of "schemas" "default__other_history" by "web_root"
    And the history lists "update" of "schemas" "default__other_history" by "web_root"
    When I delete the schema "default__other_history" by typing its name
    Then the history lists "delete" of "schemas" "default__other_history" by "web_root"

  Scenario: A table that exists cannot be added again
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_dup"
    And I add the table "p.d.dup" to the schema "default__other_dup"
    And I try to add the table "p.d.dup" to the schema "default__other_dup"
    Then I see the alert "The row clashes"

  Scenario: The partitioned strategy keeps n and fallback
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_part"
    And I add the table "p.d.part" to the schema "default__other_part"
    And I edit the table "p.d.part" to be partitioned with n "4" and fallback
    Then the table "p.d.part" shows "partitioned, n=4, fallback"

  Scenario: A full table refuses n
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_full"
    And I add the table "p.d.full" to the schema "default__other_full"
    And I try to edit the table "p.d.full" with n "3"
    Then I see the alert "The value is wrong"

  Scenario: The sort columns show in the table list
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_sort"
    And I add the table "p.d.sorted" to the schema "default__other_sort"
    And I edit the table "p.d.sorted" with sort columns "id, name"
    Then the table "p.d.sorted" shows "id, name"

  Scenario: An encrypted schema warns in the Query tab
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_encrypted"
    And I mark the schema "default__other_encrypted" as encrypted
    And I open the "Query" tab
    And I choose the schema "default__other_encrypted"
    Then I see the warning "The files of this schema are encrypted"
    And the button "Open notebook" is disabled

  Scenario: A schema keeps its claim while a table has a rule
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_claim"
    And I edit the schema "default__other_claim" with claim "cpf"
    And I add the table "p.d.claim" to the schema "default__other_claim"
    And I add a row rule for the table "p.d.claim" on column "id" with unit type "cpf"
    And I try to clear the claim of the schema "default__other_claim"
    Then I see an alert
    And the dialog stays open with an alert
    And the schema "default__other_claim" still has the claim "cpf"

  Scenario: A schema in a project takes the name of the project
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the project "e2eproj"
    And I add the schema "orders" to the project "e2eproj"
    Then the schema "orders" is listed in the project "e2eproj"
    When I delete the schema "orders" of the project "e2eproj" by typing its name
    And I delete the project "e2eproj"
    Then I see no project "e2eproj"

  Scenario: A refresh of the page does not undo an edit of the row rules
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_refresh"
    And I edit the schema "default__other_refresh" with claim "cpf"
    And I add the table "p.d.refresh" to the schema "default__other_refresh"
    And I add a row rule for the table "p.d.refresh" on column "id" with unit type "cpf"
    And I open the row rules of "p.d.refresh"
    And I delete the first condition in the editor
    And the page refreshes its data while the editor is open
    And I save the row rules
    Then I see no row rule on "p.d.refresh"
    When I delete the table "p.d.refresh"
    And I delete the schema "default__other_refresh" by typing its name
    Then I see no row of the schema "default__other_refresh"

  Scenario: Row rules combine with and, and the cards survive a reload
    Given I sign in as "web_root"
    When I open the "Config" tab
    And I add the schema "default__other_groups"
    And I edit the schema "default__other_groups" with claim "cpf"
    And I add the table "p.d.groups" to the schema "default__other_groups"
    And I build the row rules of "p.d.groups" with the keyboard: region_id of region and unit_id of unit, or area of area
    Then the editor says "A user sees a row when its area matches one of their area units, or when its region_id matches one of their region units and its unit_id matches one of their unit units."
    When I save the row rules
    And I reload the page
    And I open the row rules of "p.d.groups"
    Then I see "Group 1: all of these must match" with the columns "region_id" and "unit_id"
    And the editor says "A user sees a row when its area matches one of their area units, or when its region_id matches one of their region units and its unit_id matches one of their unit units."
    When I close the editor
    And I delete the table "p.d.groups"
    And I delete the schema "default__other_groups" by typing its name
    Then I see no row of the schema "default__other_groups"

