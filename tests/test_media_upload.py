"""A MediaLike argument is put into the datalayer through obstore."""

import io

import obstore
import pytest
from obstore.store import MemoryStore

from rekuest.api.schema import MediaUploadGrant
from rekuest.client.upload import media
from rekuest.client.upload.errors import UploadError
from rekuest.datalayer import DataLayer
from rekuest.scalars import MediaLike


def _grant(session_token: str = "session") -> MediaUploadGrant:
    return MediaUploadGrant(
        accessKey="access",
        secretKey="secret",
        sessionToken=session_token,
        path="s3://media/uploads/movie.mp4",
        key="uploads/movie.mp4",
        bucket="media",
        expiresIn=3600,
        maxBytes=1024,
        store="store-id",
    )


def _file(content: bytes) -> MediaLike:
    value = io.BytesIO(content)
    value.name = "movie.mp4"
    return MediaLike(value)


@pytest.mark.asyncio
async def test_the_file_lands_at_the_granted_key_and_the_store_is_returned(monkeypatch):
    store = MemoryStore()
    seen = {}

    def fake_store(endpoint_url, credentials, proxy=None, certificates=None):
        seen["endpoint_url"], seen["proxy"] = endpoint_url, proxy
        return store

    monkeypatch.setattr(media, "create_s3_store", fake_store)
    datalayer = DataLayer(endpoint_url="http://minio:9000", proxy="http://mesh:1055")

    returned = await media.astore_media_file(_file(b"frames"), _grant(), datalayer)

    assert returned == "store-id"
    assert bytes(obstore.get(store, "uploads/movie.mp4").bytes()) == b"frames"
    assert seen == {"endpoint_url": "http://minio:9000", "proxy": "http://mesh:1055"}


@pytest.mark.asyncio
async def test_a_failed_put_is_an_upload_error(monkeypatch):
    class Refusing:
        pass

    monkeypatch.setattr(media, "create_s3_store", lambda *a, **k: Refusing())

    with pytest.raises(UploadError, match="s3://media/uploads/movie.mp4"):
        await media.astore_media_file(
            _file(b"frames"), _grant(), DataLayer(endpoint_url="http://minio:9000")
        )


def test_the_store_is_path_style_and_carries_the_grant():
    store = media.create_s3_store(
        "http://minio:9000", _grant(), proxy="http://mesh:1055"
    )

    assert store.config["bucket"] == "media"
    assert store.config["access_key_id"] == "access"
    assert store.config["session_token"] == "session"
    assert store.config["virtual_hosted_style_request"] == "false"
    assert store.client_options == {
        "allow_http": "true",
        "proxy_url": "http://mesh:1055",
    }


def test_an_https_store_without_a_session_token_or_proxy_sets_neither():
    store = media.create_s3_store("https://s3.example.org", _grant(session_token=""))

    assert "session_token" not in store.config
    assert not store.client_options


HUB_CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIIBMzCB26ADAgECAgEBMAoGCCqGSM49BAMCMBcxFTATBgNVBAMMDEFya2l0ZWt0
IGh1YjAeFw0yNjAxMDEwMDAwMDBaFw0zNTEyMzAwMDAwMDBaMBcxFTATBgNVBAMM
DEFya2l0ZWt0IGh1YjBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABOipTSOE+oBd
xKJHSWVaGWLqUmf8wK9hqIwK53vQupHv4EY05DnvYatyVaWZNFSSABXJyjVkUnCL
BQNljdPSRdajGDAWMBQGA1UdEQQNMAuCCWxvY2FsaG9zdDAKBggqhkjOPQQDAgNH
ADBEAiB3vd12o38Qp39iZKng0rOWnZkuBcIJGbVQxjImnPeo5wIgSGM06akz+rlV
l0hiBf+cli9Wi5pinz2yRynHAGCXoE8=
-----END CERTIFICATE-----
"""
"""A self-signed hub certificate, public half only: what a pinned alias carries."""


def test_a_pinned_store_trusts_the_hub_certificate_alone():
    """Per store, not via SSL_CERT_FILE: obstore >= 0.11 never reads it on macOS or Windows."""
    store = media.create_s3_store(
        "https://localhost:2577", _grant(), certificates=[HUB_CERTIFICATE]
    )

    assert store.client_options["root_certificate"] == HUB_CERTIFICATE.strip().encode()
    assert str(store.client_options["disable_system_certificates"]).lower() == "true"
