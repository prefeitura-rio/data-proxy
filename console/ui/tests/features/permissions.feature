Feature: Pages by role

  Scenario Outline: A role opens a page directly
    Given I am signed in as "<user>"
    When I open the address "<address>" directly
    Then the answer is <status>

    Examples:
      | user        | address                                          | status |
      | web_admin   | /sync                                            | 200    |
      | web_admin   | /config                                          | 200    |
      | web_admin   | /access?scope=default.test                       | 200    |
      | web_admin   | /query                                           | 200    |
      | web_admin   | /sync/runs/00000000-0000-4000-8000-0000000000f1  | 200    |
      | web_console  | /sync                                            | 200    |
      | web_console  | /config                                          | 200    |
      | web_console  | /access                                          | 403    |
      | web_console  | /query                                           | 403    |
      | web_console  | /sync/runs/00000000-0000-4000-8000-0000000000f1  | 200    |
      | web_policy  | /sync                                            | 403    |
      | web_policy  | /config                                          | 200    |
      | web_policy  | /access                                          | 200    |
      | web_policy  | /query                                           | 403    |
      | web_policy  | /sync/runs/00000000-0000-4000-8000-0000000000f1  | 403    |
      | web_api     | /sync                                            | 403    |
      | web_api     | /config                                          | 403    |
      | web_api     | /access                                          | 403    |
      | web_api     | /query                                           | 403    |
      | web_other   | /sync                                            | 200    |
      | web_other   | /config                                          | 200    |
      | web_other   | /query                                           | 403    |
      | web_other   | /sync/runs/00000000-0000-4000-8000-0000000000f1  | 404    |

  Scenario: A console user has the Start button and no Query tab
    Given I sign in as "web_console"
    Then I see the "Sync" tab
    And I do not see the "Query" tab
    And I see the "Start run" button

  Scenario: A role without the start permission gets 403 on a direct call
    Given I sign in as "web_api"
    When I post a run request directly
    Then the answer is 403

  Scenario: A user with another schemas claim sees no rows of the schema
    Given I sign in as "web_other"
    When I open the "Config" tab
    Then I see no row of the schema "default__test"
