import pytest

from tests.constants import POSTGREST_URL
from tests.fixtures.types import Postgrest


@pytest.fixture(scope="session")
def postgrest() -> Postgrest:
    """Provide the PostgREST endpoint that devenv backs with the test database."""
    return Postgrest(POSTGREST_URL)
