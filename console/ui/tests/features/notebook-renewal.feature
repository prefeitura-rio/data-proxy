Feature: The notebook gets renewed links

  Scenario: The console renews the links before they expire
    Given I sign in as "web_admin"
    When I open the "Query" tab
    And I choose the table "rj-ia-desenvolvimento.dev.full_table" of the schema "test"
    And I open the notebook
    And I run the cells of the notebook
    Then the notebook has applied the grant
    And the notebook gets a renewed grant within 60 seconds
    When the first grant has expired
    And I count the rows of the table "rj-ia-desenvolvimento.dev.full_table" in the notebook
    Then the notebook shows the number of rows that the database counts
