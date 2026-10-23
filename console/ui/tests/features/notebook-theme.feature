Feature: The notebook follows the theme of the console

  Scenario: The notebook changes with the theme that the user picks
    Given the browser prefers the "light" theme
    And I sign in as "web_admin"
    When I open the "Query" tab
    And I choose the table "rj-ia-desenvolvimento.dev.full_table" of the schema "test"
    And I open the notebook
    Then the notebook uses the "light" theme
    When I switch to the "dark" theme
    Then the notebook uses the "dark" theme
    When I switch to the "light" theme
    Then the notebook uses the "light" theme
