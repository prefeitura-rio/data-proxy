Feature: Pages by role

  Scenario Outline: A role opens a page directly
    Given I am signed in as "<user>"
    When I open the address "<address>" directly
    Then the answer is <status>

    Examples:
      | user        | address                                          | status |
      | web_admin   | /sync                                            | 200    |
      | web_admin   | /config                                          | 200    |
      | web_admin   | /access                                          | 200    |
      | web_admin   | /query                                           | 200    |
      | web_admin   | /sync/runs/00000000-0000-4000-8000-0000000000f1  | 200    |
      | web_viewer  | /sync                                            | 200    |
      | web_viewer  | /config                                          | 200    |
      | web_viewer  | /access                                          | 403    |
      | web_viewer  | /query                                           | 403    |
      | web_viewer  | /sync/runs/00000000-0000-4000-8000-0000000000f1  | 200    |
      | web_policy  | /sync                                            | 403    |
      | web_policy  | /config                                          | 403    |
      | web_policy  | /access                                          | 200    |
      | web_policy  | /query                                           | 403    |
      | web_policy  | /sync/runs/00000000-0000-4000-8000-0000000000f1  | 403    |
      | web_api     | /sync                                            | 403    |
      | web_api     | /config                                          | 403    |
      | web_api     | /access                                          | 403    |
      | web_api     | /query                                           | 403    |
      | web_other   | /sync                                            | 200    |
      | web_other   | /config                                          | 200    |
      | web_other   | /query                                           | 200    |
      | web_other   | /sync/runs/00000000-0000-4000-8000-0000000000f1  | 404    |
