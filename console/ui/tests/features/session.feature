Feature: Sign-in and session

  Scenario: The health check needs no session
    Then the health check answers without a session

  Scenario: An expired session returns to sign-in
    Given I sign in as "web_admin"
    When the session cookie is damaged and the identity provider has no session
    And I reload the page
    Then I am on the sign-in page

  @fresh-login
  Scenario: A user signs out
    Given I sign in as "web_admin"
    When I sign out
    Then I am on the sign-in page
    When I open the seeded run
    Then I am on the sign-in page

  Scenario: A role with no console permission sees the no-access page
    Given I sign in as "web_api"
    Then I am on the no-access page
    And I do not see the "Sync" tab

  Scenario: A token without a role sees the no-access page and does not loop
    Given I sign in as "web_norole"
    Then I am on the no-access page
    And I do not see the "Sync" tab
