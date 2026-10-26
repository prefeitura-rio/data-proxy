@new-schema
Feature: Scenarios that add, change and delete a schema of their own

  Scenario: Schema delete needs the typed name
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other_delete"
    And I ask to delete the schema "other_delete"
    Then the confirm button stays disabled until I type "other_delete"
    When I confirm the deletion
    Then I see no row of the schema "other_delete"

  Scenario: A schema, a table and a row rule go through their whole life
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other_life"
    And I edit the schema "other_life" with claim "cpf"
    And I add the table "p.d.life" to the schema "other_life"
    And I edit the table "p.d.life" with cache TTL "120"
    Then the table "p.d.life" shows cache TTL "120"
    When I add a row rule for the table "p.d.life" on column "id" with unit type "cpf"
    And I change the unit type of the rule on "p.d.life" to "cnpj"
    Then I see a row rule on "p.d.life" with unit type "cnpj"
    When I delete the row rule of "p.d.life"
    Then I see no row rule on "p.d.life"
    When I delete the table "p.d.life"
    Then I see no table "p.d.life"
    When I delete the schema "other_life" by typing its name
    Then I see no row of the schema "other_life"

  Scenario: A change shows in the history with the user name
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other_history"
    And I edit the schema "other_history" with claim "cpf"
    Then the history lists "insert" of "schemas" "other_history" by "web_other"
    And the history lists "update" of "schemas" "other_history" by "web_other"
    When I delete the schema "other_history" by typing its name
    Then the history lists "delete" of "schemas" "other_history" by "web_other"

  Scenario: A table that exists cannot be added again
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other_dup"
    And I add the table "p.d.dup" to the schema "other_dup"
    And I try to add the table "p.d.dup" to the schema "other_dup"
    Then I see the alert "The row clashes"

  Scenario: The partitioned strategy keeps n and fallback
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other_part"
    And I add the table "p.d.part" to the schema "other_part"
    And I edit the table "p.d.part" to be partitioned with n "4" and fallback
    Then the table "p.d.part" shows "partitioned, n=4, fallback"

  Scenario: A full table refuses n
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other_full"
    And I add the table "p.d.full" to the schema "other_full"
    And I try to edit the table "p.d.full" with n "3"
    Then I see the alert "The value is wrong"

  Scenario: The sort columns show in the table list
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other_sort"
    And I add the table "p.d.sorted" to the schema "other_sort"
    And I edit the table "p.d.sorted" with sort columns "id, name"
    Then the table "p.d.sorted" shows "id, name"

  Scenario: An encrypted schema warns in the Query tab
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other_encrypted"
    And I mark the schema "other_encrypted" as encrypted
    And I open the "Query" tab
    And I choose the schema "other_encrypted"
    Then I see the warning "The files of this schema are encrypted"
    And the button "Open notebook" is disabled

  Scenario: A schema keeps its claim while a table has a rule
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other_claim"
    And I edit the schema "other_claim" with claim "cpf"
    And I add the table "p.d.claim" to the schema "other_claim"
    And I add a row rule for the table "p.d.claim" on column "id" with unit type "cpf"
    And I try to clear the claim of the schema "other_claim"
    Then I see an alert
    And the dialog stays open with an alert
    And the schema "other_claim" still has the claim "cpf"
