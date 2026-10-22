Feature: Security of the console

  Scenario: A request from another origin is refused
    Given I sign in as "web_admin"
    When I post a run request from the origin "https://evil.example"
    Then the answer is 403

  Scenario: An address without a session leads to the sign-in page
    Then an address without a session leads to the sign-in page

  @short-token
  Scenario: The console refreshes a token that is about to expire
    Given the identity provider issues tokens that last 60 seconds
    And I sign in as "web_admin"
    When I remember the session cookies
    And I reload the page
    Then I see the "Sync" tab
    And the session cookies are not the remembered ones
