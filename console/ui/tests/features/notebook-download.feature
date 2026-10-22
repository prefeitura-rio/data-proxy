Feature: The notebook download

  Scenario: The notebook that I download holds no link
    Given I sign in as "web_admin"
    When I open the "Query" tab
    And I choose the table "rj-ia-desenvolvimento.dev.full_table" of the schema "test"
    And I open the notebook
    And I run the cells of the notebook
    Then the notebook has applied the grant
    When I save the notebook
    And I download the notebook as Python
    Then the downloaded file has the cells and no signature of a link
