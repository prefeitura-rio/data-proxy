Feature: The Config tab

  Scenario: A changed document previews its updates and applies nothing
    Given I sign in as "web_admin"
    When I open the "Config" tab
    And I export the configuration
    And I set the cache TTL of every table in the exported file to 321
    And I preview the import of the exported file
    Then the preview lists an "update"
    And no table shows the cache TTL "321"

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

  Scenario: A broken document is refused
    Given I sign in as "web_admin"
    When I open the "Config" tab
    And I preview the import of a file with the text "{not json"
    Then I see the alert "The file is not valid JSON."

  Scenario: The history pages and filters by object
    Given I sign in as "web_admin"
    When I open the "Config" tab
    Then the list with the column "Action" says "1 to 25 of"
    When I go to the "Older" rows of the list with the column "Action"
    Then the list with the column "Action" says "26 to"
    When I set "Object" to "schemas" in the form "Filter the history" and submit it
    Then every row of the list with the column "Action" shows "schemas"
