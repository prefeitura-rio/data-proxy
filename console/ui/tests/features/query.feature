Feature: The query tab

  Scenario: The tab selects every table of a schema and remembers the schema
    Given I sign in as "web_admin"
    When I open the "Query" tab
    And I choose the schema "test"
    Then I see the hint "Choose at least one table."
    And the button "Open notebook" is disabled
    When I select all the tables
    Then the button "Open notebook" is enabled
    When I reload the page
    Then the schema "test" is chosen
