Feature: The theme of the console

  Scenario: The console follows the theme of the browser
    Given the browser prefers the "dark" theme
    And I sign in as "web_admin"
    Then the page uses the "dark" theme

  Scenario: A choice of the user stays after a reload
    Given the browser prefers the "light" theme
    And I sign in as "web_admin"
    When I switch to the "dark" theme
    Then the page uses the "dark" theme
    When I reload the page
    Then the page uses the "dark" theme
    When I switch to the "light" theme
    And I reload the page
    Then the page uses the "light" theme
