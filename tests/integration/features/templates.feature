@postgres
Feature: Helm SQL template execution

  Scenario: Rendering Helm maintenance templates creates PostgreSQL procedures
    Given a PostgreSQL database for Helm template rendering
    When I render and execute the Helm maintenance templates
    Then the Helm maintenance procedures exist
