"""An app's registered files reach the server's asset store once it has registered.

Against the docker stack: registering tells the server which files the agent brings,
and only then does the server take their upload, which is why the upload follows.
"""

import asyncio
import io
import struct
from pathlib import Path

import pytest
from dokker import Deployment

from arkitekt_spec.declare.app import AppRegistry
import kulisse
from rekuest.api.schema import CreateAssetMutation
from rekuest.client.upload.assets import aupload_assets
from rekuest.client.upload.media import astore_media_file
from rekuest.datalayer import DataLayer
from rekuest.scalars import MediaLike

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest
from .test_asset_upload_integration_arm import ARM

pytest.importorskip("trimesh", reason="authoring the model needs the kulisse extra")

PNG = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 64, 32) + b"\x08\x06\x00\x00\x00rest"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_registered_assets_are_stored_after_the_app_registered(deployment: Deployment, integration_ports: dict[str, int], tmp_path: Path) -> None:
    (tmp_path / "arm.urdf").write_text(ARM)
    (tmp_path / "arm.glb").write_bytes(kulisse.import_urdf(tmp_path / "arm.urdf"))
    (tmp_path / "logo.png").write_bytes(PNG)

    app = build_fresh_rekuest(deployment, token="standalone_token")
    registry: AppRegistry = app.registry
    registry.root = tmp_path
    registry.register_asset("arm", "arm.glb")
    registry.register_asset("logo", "logo.png")
    registry.register_blok("arm_view", '<Scene><Model src="@self.assets.arm" /></Scene>', context="scene")
    refs = {name: asset.ref for name, asset in registry.registered_assets.items()}
    datalayer = DataLayer(endpoint_url=f"http://localhost:{integration_ports['rustfs']}")
    uploads = 0

    async def upload() -> None:
        nonlocal uploads
        uploads += 1
        await aupload_assets(app.client.rath, datalayer, registry.registered_assets.values())

    app.agent.after_registration = upload

    async with app:
        # Before the agent registered the server knows of no such files, and takes none.
        assert await app.astored_asset(refs["arm"]) is None, "the stack already holds the model"
        # (The stack's static tokens are admins, so the refusal names what an admin may upload.)
        with pytest.raises(Exception, match="names a source for the asset"):
            await app.arequest_asset_upload(refs["arm"])

        await app.aconnect(timeout=CONNECT_TIMEOUT)
        loop = asyncio.create_task(app.aloop())
        try:
            async with asyncio.timeout(30):
                while not all([await app.astored_asset(ref) for ref in refs.values()]):
                    await asyncio.sleep(0.2)
        finally:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)

        stored = {name: await app.astored_asset(ref) for name, ref in refs.items()}

    assert {name: (asset.ref, asset.kind) for name, asset in stored.items()} == {"arm": (refs["arm"], "MODEL_3D"), "logo": (refs["logo"], "IMAGE")}
    assert uploads == 1


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_file_an_app_names_a_source_for_is_added_by_an_admin(deployment: Deployment, integration_ports: dict[str, int], tmp_path: Path) -> None:
    """The app holds no bytes and uploads none; an admin's client brings them, as it would after fetching the source."""
    (tmp_path / "arm.urdf").write_text(ARM)
    # A different file than the first test's, so the stack does not hold it yet.
    data = kulisse.with_joints(kulisse.import_urdf(tmp_path / "arm.urdf"), [])
    ref = kulisse.model_ref(data)

    app = build_fresh_rekuest(deployment, token="btest_token")
    app.registry.register_asset("arm", source="https://vendor.example/models/arm.glb", ref=ref)
    app.registry.register_blok("arm_view", '<Scene><Model src="@self.assets.arm" /></Scene>', context="scene")
    assert app.agent.after_registration is None

    admin = build_fresh_rekuest(deployment, token="atest_token")
    member = build_fresh_rekuest(deployment, token="member_token")
    datalayer = DataLayer(endpoint_url=f"http://localhost:{integration_ports['rustfs']}")

    async with app, admin, member:
        assert await app.astored_asset(ref) is None, "the stack already holds the model"
        with pytest.raises(Exception, match="names a source"):
            await admin.arequest_asset_upload(ref, size=len(data))

        await app.aconnect(timeout=CONNECT_TIMEOUT)
        loop = asyncio.create_task(app.aloop())
        try:
            # Whoever is no admin uploads only what their own agent declares.
            with pytest.raises(Exception, match="declares no asset"):
                await member.arequest_asset_upload(ref, size=len(data))

            grant = await admin.arequest_asset_upload(ref, size=len(data))
            file = io.BytesIO(data)
            file.name = "arm.glb"
            store = await astore_media_file(MediaLike(file), grant, datalayer)
            # The store's id, not a file: the bytes are where the grant put them already.
            await admin.rath.aquery(CreateAssetMutation.Meta.document, {"input": {"ref": ref, "store": store}})
        finally:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)

        stored = await app.astored_asset(ref)

    assert stored is not None and (stored.ref, stored.kind) == (ref, "MODEL_3D")
