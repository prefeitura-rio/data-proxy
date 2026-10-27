from logging import getLogger

logger = getLogger("data_proxy")


def pytest_bdd_before_scenario(feature: object, scenario: object) -> None:
    """Log before a BDD scenario starts."""
    logger.info(
        "BDD scenario started: %s > %s",
        getattr(feature, "name", str(feature)),
        getattr(scenario, "name", str(scenario)),
    )


def pytest_bdd_after_scenario(feature: object, scenario: object) -> None:
    """Log after a BDD scenario completes."""
    logger.info(
        "BDD scenario completed: %s > %s",
        getattr(feature, "name", str(feature)),
        getattr(scenario, "name", str(scenario)),
    )


pytest_plugins = [
    "tests.fixtures.api",
    "tests.fixtures.console",
    "tests.fixtures.db",
    "tests.fixtures.dbos",
    "tests.fixtures.pipeline",
    "tests.fixtures.routing",
    "tests.integration.steps.state",
]
