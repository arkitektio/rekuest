import pytest
from .integration.utils import wait_for_http_response
from .utils import build_relative
from testcontainers.compose import DockerCompose
from rekuest.definition.define import prepare_definition
from .funcs import complex_karl


@pytest.mark.integration
@pytest.fixture(scope="session")
def environment():
    with DockerCompose(
        filepath=build_relative("integration"),
        compose_file_name="docker-compose.yaml",
    ) as compose:
        wait_for_http_response("http://localhost:8019/ht", max_retries=5)
        wait_for_http_response("http://localhost:8098/ht", max_retries=5)
        yield


@pytest.mark.integration
@pytest.fixture
def app():
    from herre.fakts import FaktsHerre
    from fakts.grants.remote.static import ClaimGrant
    from fakts.grants.remote.base import StaticDiscovery
    from arkitekt.apps.rekuest import RekuestApp
    from fakts.fakts import Fakts

    return RekuestApp(
        fakts=Fakts(
            grant=ClaimGrant(
                client_id="DSNwVKbSmvKuIUln36FmpWNVE2KrbS2oRX0ke8PJ",
                client_secret="Gp3VldiWUmHgKkIxZjL2aEjVmNwnSyIGHWbQJo6bWMDoIUlBqvUyoGWUWAe6jI3KRXDOsD13gkYVCZR0po1BLFO9QT4lktKODHDs0GyyJEzmIjkpEOItfdCC4zIa3Qzu",
                graph="localhost",
                discovery=StaticDiscovery(base_url="http://localhost:8019/f/"),
            ),
            force_refresh=True,
        ),
        herre=FaktsHerre(no_temp=True),
    )
