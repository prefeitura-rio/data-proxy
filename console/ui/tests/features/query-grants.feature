Feature: The grants of the query tab

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

  Scenario: A grant lists the files of two tables
    Given I sign in as "web_admin"
    When I request a grant for the schema "test" and the tables "rj-ia-desenvolvimento.dev.full_table,rj-ia-desenvolvimento.dev.snapshot_table"
    Then the answer is 200
    And the grant lists 2 tables
