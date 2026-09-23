@redis
Feature: Response cache invalidation

  Scenario: Clearing the response cache removes stored data
    Given a real response cache
    When I clear the response cache
    Then the response cache is empty
