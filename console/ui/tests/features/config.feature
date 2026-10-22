Feature: Deleting a schema

  Scenario: Schema delete needs the typed name
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other"
    And I ask to delete the schema "other"
    Then the confirm button stays disabled until I type "other"
    When I confirm the deletion
    Then I see no row of the schema "other"

  Scenario: A schema, a table and a row rule go through their whole life
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other"
    And I edit the schema "other" with claim "cpf"
    And I add the table "p.d.rows" to the schema "other"
    And I edit the table "p.d.rows" with cache TTL "120"
    Then the table "p.d.rows" shows cache TTL "120"
    When I add a row rule for the table "p.d.rows" on column "id" with unit type "cpf"
    And I change the unit type of the rule on "p.d.rows" to "cnpj"
    Then I see a row rule on "p.d.rows" with unit type "cnpj"
    When I delete the row rule of "p.d.rows"
    Then I see no row rule on "p.d.rows"
    When I delete the table "p.d.rows"
    Then I see no table "p.d.rows"
    When I delete the schema "other" by typing its name
    Then I see no row of the schema "other"

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

  Scenario: A changed document previews its updates and applies nothing
    Given I sign in as "web_admin"
    When I open the "Config" tab
    And I export the configuration
    And I set the cache TTL of every table in the exported file to 321
    And I preview the import of the exported file
    Then the preview lists an "update"
    And no table shows the cache TTL "321"

  Scenario: A change shows in the history with the user name
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other"
    And I edit the schema "other" with claim "cpf"
    Then the history lists "insert" of "schemas" "other" by "web_other"
    And the history lists "update" of "schemas" "other" by "web_other"
    When I delete the schema "other" by typing its name
    Then the history lists "delete" of "schemas" "other" by "web_other"

  Scenario: The history shows only the schemas of the user
    Given I sign in as "web_admin"
    When I open the "Config" tab
    Then the history lists no row of the schema "other"

  Scenario: A console user reads the configuration and changes nothing
    Given I sign in as "web_viewer"
    When I open the "Config" tab
    Then I see the row of the schema "test"
    And I do not see the "Add schema" button
    And I do not see the "Edit" button
    And I do not see the "Delete" button
    And I do not see the "Import" button

  Scenario: The table list filters by name
    Given I sign in as "web_admin"
    When I open the "Config" tab
    And I set "Name contains" to "full_table" in the form "Filter the tables" and submit it
    Then every row of the list with the column "Strategy" shows "full_table"

  Scenario: A schema outside the claim is refused
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I try to add the schema "elsewhere"
    Then I see the alert "You aren't allowed to do this."
    And I see no row of the schema "elsewhere"

  @other-schema
  Scenario: A table that exists cannot be added again
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other"
    And I add the table "p.d.dup" to the schema "other"
    And I try to add the table "p.d.dup" to the schema "other"
    Then I see the alert "The row clashes"

  @other-schema
  Scenario: The partitioned strategy keeps n and fallback
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other"
    And I add the table "p.d.part" to the schema "other"
    And I edit the table "p.d.part" to be partitioned with n "4" and fallback
    Then the table "p.d.part" shows "partitioned, n=4, fallback"

  @other-schema
  Scenario: A full table refuses n
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other"
    And I add the table "p.d.full" to the schema "other"
    And I try to edit the table "p.d.full" with n "3"
    Then I see the alert "The value is wrong"

  @other-schema
  Scenario: The sort columns show in the table list
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other"
    And I add the table "p.d.sorted" to the schema "other"
    And I edit the table "p.d.sorted" with sort columns "id, name"
    Then the table "p.d.sorted" shows "id, name"

  @other-schema
  Scenario: An encrypted schema warns in the Query tab
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other"
    And I mark the schema "other" as encrypted
    And I open the "Query" tab
    And I choose the schema "other"
    Then I see the warning "The files of this schema are encrypted"
    And the button "Open notebook" is disabled

  @other-schema
  Scenario: A schema keeps its claim while a table has a rule
    Given I sign in as "web_other"
    When I open the "Config" tab
    And I add the schema "other"
    And I edit the schema "other" with claim "cpf"
    And I add the table "p.d.rows" to the schema "other"
    And I add a row rule for the table "p.d.rows" on column "id" with unit type "cpf"
    And I try to clear the claim of the schema "other"
    Then I see an alert
    And the schema "other" still has the claim "cpf"

  Scenario: A broken document is refused
    Given I sign in as "web_admin"
    When I open the "Config" tab
    And I preview the import of a file with the text "{not json"
    Then I see the alert "The file is not valid JSON."

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

  Scenario: The history pages and filters by object
    Given I sign in as "web_admin"
    When I open the "Config" tab
    Then the list with the column "Action" says "1 to 25 of"
    When I go to the "Older" rows of the list with the column "Action"
    Then the list with the column "Action" says "26 to"
    When I set "Object" to "schemas" in the form "Filter the history" and submit it
    Then every row of the list with the column "Action" shows "schemas"
