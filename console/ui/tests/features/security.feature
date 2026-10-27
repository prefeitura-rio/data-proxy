Feature: Security of the console

  Scenario: A request from another origin is refused
    Given I sign in as "web_admin"
    When I post a run request from the origin "https://evil.example"
    Then the answer is 403

  Scenario: An address without a session leads to the sign-in page
    Then an address without a session leads to the sign-in page

  Scenario: A page carries the security headers
    Given I sign in as "web_admin"
    When I load the page "/sync"
    Then the page has the header "x-content-type-options" with the value "nosniff"
    And the page has the header "referrer-policy" with the value "same-origin"
    And the page has the header "cache-control" with the value "no-store"
    And the page has the header "content-security-policy" with a value that contains "frame-ancestors 'none'"
    And the page has the header "content-security-policy" with a value that contains "frame-src 'self' http"

