"""Uploading the files an app registered as assets, once the server knows them.

An app registers a local file under a name (``app.register_asset``) and names it in a
blok (``@self.assets.<name>``). Registering tells the server which files the agent
brings; this then puts the ones the server does not hold into the datalayer, with a
grant issued for exactly that file, and records them, which is where the server reads
each back and holds it to the rules of its kind. A refusal never stops the app: its
bloks are registered either way, and a viewer shows the asset as missing.
"""

import hashlib
import logging
from collections.abc import Iterable
from pathlib import Path

import rath
from rath.operation import GraphQLException

from arkitekt_spec.declare.app import AssetDeclaration
from rekuest.api.schema import (
    CreateAssetMutation,
    RequestAssetUploadMutation,
    StoredAssetQuery,
)
from rekuest.client.upload.errors import UploadError
from rekuest.client.upload.media import astore_media_file
from rekuest.datalayer import DataLayer
from rekuest.scalars import MediaLike

logger = logging.getLogger(__name__)


async def _aupload(
    client: rath.Rath, datalayer: DataLayer, asset: AssetDeclaration, path: Path
) -> bool:
    """Put one asset into the server's store; whether anything was uploaded."""
    stored = await client.aquery(StoredAssetQuery.Meta.document, {"ref": asset.ref})
    if StoredAssetQuery.model_validate(stored.data).asset is not None:
        return False

    data = path.read_bytes()
    if "sha256:" + hashlib.sha256(data).hexdigest() != asset.ref:
        raise UploadError(f"{path} changed since it was registered as '{asset.name}'")

    granted = await client.aquery(
        RequestAssetUploadMutation.Meta.document, {"input": {"ref": asset.ref}}
    )
    grant = RequestAssetUploadMutation.model_validate(granted.data).request_asset_upload
    with path.open("rb") as file:
        store = await astore_media_file(MediaLike(file), grant, datalayer)

    # The store's id, not a file: the bytes are where the grant put them already.
    await client.aquery(
        CreateAssetMutation.Meta.document, {"input": {"ref": asset.ref, "store": store}}
    )
    return True


def _refusal_code(error: GraphQLException) -> str | None:
    """The code a server refusal carries in its first error's extensions."""
    for item in error.errors or ():
        code = (item.get("extensions") or {}).get("code")
        if code:
            return str(code)
    return None


async def aupload_assets(
    client: rath.Rath, datalayer: DataLayer, assets: Iterable[AssetDeclaration]
) -> None:
    """Upload every asset the app holds and the server does not, one after the other.

    Each is tried on its own: one the server refuses, or whose file is gone, is
    logged with its reason and the rest still go. An asset the app only names a
    source for is not the app's to upload: an admin adds it from their client.
    """
    for asset in assets:
        if asset.path is None:
            logger.debug("The %s '%s' is fetched from %s, not uploaded by the app", asset.kind.label, asset.name, asset.source)
            continue
        what = f"{asset.kind.label} '{asset.name}' ({asset.path.name})"
        try:
            uploaded = await _aupload(client, datalayer, asset, asset.path)
        except GraphQLException as error:
            logger.warning(
                "The server refused the %s (%s): %s",
                what,
                _refusal_code(error) or "no code",
                error.message,
            )
        except (UploadError, OSError) as error:
            logger.warning("Could not upload the %s: %s", what, error)
        else:
            if uploaded:
                logger.info("Uploaded the %s", what)
