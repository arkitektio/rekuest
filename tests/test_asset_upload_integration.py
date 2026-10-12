"""An app's registered files reach the server's asset store once it has registered.

Against the docker stack: registering tells the server which files the agent brings,
and only then does the server take their upload, which is why the upload follows.
"""

import asyncio
import struct
from pathlib import Path

import pytest
from dokker import Deployment

from arkitekt_spec.declare.app import AppRegistry
from rekuest import kulisse
from rekuest.client.upload.assets import aupload_assets
from rekuest.datalayer import DataLayer

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest
from .kulisse.test_kulisse import ARM

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
        with pytest.raises(Exception, match="declares no asset"):
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
