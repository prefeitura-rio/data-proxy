Feature: The query tab

  Scenario: A schema outside the claim is refused
    Given I sign in as "web_admin"
    When I request a grant for the schema "e2e_probe" and the table "p.d.t"
    Then the answer is 403

  Scenario: The links of a grant read only their own file
    Given I sign in as "web_admin"
    When I request a grant for the schema "test" and the table "rj-ia-desenvolvimento.dev.full_table"
    Then the answer is 200
    And the links are signed with an identity of the schema, not with the shared reader
    And the first link of the grant is readable
    And the same link with another key answers 403
    And the same link with a changed signature answers 403

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

  Scenario: The console renews the links before they expire
    Given I sign in as "web_admin"
    When I open the "Query" tab
    And I choose the table "rj-ia-desenvolvimento.dev.full_table" of the schema "test"
    And I open the notebook
    And I run the cells of the notebook
    Then the notebook has applied the grant
    And the notebook gets a renewed grant within 5 minutes
    When the first grant has expired
    And I count the rows of the table "rj-ia-desenvolvimento.dev.full_table" in the notebook
    Then the notebook shows the number of rows that the database counts

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
