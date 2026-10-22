Feature: The notebook counts the rows

  Scenario: The notebook counts the rows that the database counts, with the delete files applied
    Given I sign in as "web_admin"
    When I open the "Query" tab
    And I choose the table "rj-ia-desenvolvimento.dev.full_table" of the schema "test"
    And I open the notebook
    Then the grant has a delete file
    When I run the cells of the notebook
    Then the notebook has applied the grant
    When I count the rows of the table "rj-ia-desenvolvimento.dev.full_table" in the notebook
    Then the notebook shows the number of rows that the database counts
