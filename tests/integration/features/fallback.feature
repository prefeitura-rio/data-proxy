@postgres @redis
Feature: Fallback views and cache invalidation

  Background:
    Given a fresh PostgreSQL fallback schema

  Scenario: Running fallback creation with fallback disabled creates no views
    When I run fallback view creation with fallback disabled
    Then no fallback views are created

  Scenario: Running fallback creation with scalar columns creates a function and view
    When I run fallback view creation with scalar columns
    Then a scalar fallback function exists
    And a scalar fallback view exists
    And the user role has select access on the view

  Scenario: Running fallback creation with nested columns casts to JSONB
    When I run fallback view creation with nested columns
    Then a nested fallback function exists
    And nested columns are cast to JSONB in the view

  Scenario: Running fallback creation with no columns fails
    When I run fallback view creation with no columns
    Then fallback creation fails with no columns error
