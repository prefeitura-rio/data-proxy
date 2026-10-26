Feature: Security of the console

  Scenario: A request from another origin is refused
    Given I sign in as "web_admin"
    When I post a run request from the origin "https://evil.example"
    Then the answer is 403

  Scenario: An address without a session leads to the sign-in page
    Then an address without a session leads to the sign-in page
