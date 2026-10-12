"""The files an app registered as assets are uploaded when the server lacks them."""

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import obstore
import pytest
from obstore.store import MemoryStore
from rath.operation import GraphQLException

from arkitekt_spec.declare.app import AppRegistry
from rekuest.client.upload import assets, media
from rekuest.datalayer import DataLayer

GLB = b"glTF" + b"\x02\x00\x00\x00" + b"model"
PNG = b"\x89PNG\r\n\x1a\n" + b"image"


class FakeServer:
    """Answers the three asset-store operations, and records what it was asked."""

    def __init__(self, stored: frozenset[str] = frozenset(), refuse: str | None = None) -> None:
        self.stored = set(stored)
        self.refuse = refuse
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def aquery(self, document: str, variables: dict[str, Any]) -> SimpleNamespace:
        name = next(n for n in ("StoredAsset", "RequestAssetUpload", "CreateAsset") if f" {n}(" in document)
        self.asked.append((name, variables))
        if name == "StoredAsset":
            ref = variables["ref"]
            return SimpleNamespace(data={"asset": {"id": "1", "ref": ref, "kind": "MODEL_3D"} if ref in self.stored else None})
        if self.refuse:
            raise GraphQLException("refused", errors=[{"message": "refused", "extensions": {"code": self.refuse}}])
        ref = variables["input"]["ref"]
        if name == "RequestAssetUpload":
            return SimpleNamespace(
                data={
                    "requestAssetUpload": {
                        "accessKey": "access",
                        "secretKey": "secret",
                        "sessionToken": "session",
                        "path": f"s3://media/{ref}",
                        "key": ref,
                        "bucket": "media",
                        "expiresIn": 3600,
                        "maxBytes": 1024,
                        "store": f"store-of-{ref[-4:]}",
                    }
                }
            )
        self.stored.add(ref)
        return SimpleNamespace(data={"createAsset": {"id": "1", "ref": ref, "kind": "MODEL_3D"}})


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    store = MemoryStore()
    monkeypatch.setattr(media, "create_s3_store", lambda *args, **kwargs: store)
    return store


@pytest.fixture
def registry(tmp_path: Path) -> AppRegistry:
    (tmp_path / "scope.glb").write_bytes(GLB)
    (tmp_path / "logo.png").write_bytes(PNG)
    registry = AppRegistry(root=tmp_path)
    registry.register_asset("scope", "scope.glb")
    registry.register_asset("logo", "logo.png")
    return registry


async def _upload(server: FakeServer, registry: AppRegistry) -> None:
    await assets.aupload_assets(server, DataLayer(endpoint_url="http://minio:9000"), registry.registered_assets.values())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_missing_assets_are_put_where_their_grants_point_and_recorded_by_store(registry: AppRegistry, bucket: MemoryStore) -> None:
    scope, logo = registry.registered_assets["scope"].ref, registry.registered_assets["logo"].ref
    server = FakeServer()

    await _upload(server, registry)

    assert bytes(obstore.get(bucket, scope).bytes()) == GLB and bytes(obstore.get(bucket, logo).bytes()) == PNG
    assert server.asked[:3] == [
        ("StoredAsset", {"ref": scope}),
        ("RequestAssetUpload", {"input": {"ref": scope}}),
        ("CreateAsset", {"input": {"ref": scope, "store": f"store-of-{scope[-4:]}"}}),
    ]
    assert server.stored == {scope, logo}


@pytest.mark.asyncio
async def test_an_asset_the_server_holds_is_not_uploaded_again(registry: AppRegistry, bucket: MemoryStore) -> None:
    held = frozenset(asset.ref for asset in registry.registered_assets.values())
    server = FakeServer(stored=held)

    await _upload(server, registry)

    assert [name for name, _ in server.asked] == ["StoredAsset", "StoredAsset"]
    assert obstore.list(bucket).collect() == []


@pytest.mark.asyncio
async def test_a_refusal_is_logged_with_its_code_and_nothing_is_raised(registry: AppRegistry, bucket: MemoryStore, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=assets.__name__):
        await _upload(FakeServer(refuse="not_declared"), registry)

    assert "refused the 3D model 'scope' (scope.glb) (not_declared)" in caplog.text
    assert "refused the image 'logo' (logo.png) (not_declared)" in caplog.text
    assert obstore.list(bucket).collect() == []


@pytest.mark.asyncio
async def test_a_file_that_changed_since_it_was_registered_is_not_uploaded(registry: AppRegistry, bucket: MemoryStore, caplog: pytest.LogCaptureFixture) -> None:
    registry.registered_assets["scope"].path.write_bytes(b"glTF something else")
    server = FakeServer()

    with caplog.at_level(logging.WARNING, logger=assets.__name__):
        await _upload(server, registry)

    assert "changed since it was registered as 'scope'" in caplog.text
    assert [name for name, _ in server.asked if name != "StoredAsset"] == ["RequestAssetUpload", "CreateAsset"], "the image still goes"
