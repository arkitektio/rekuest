"""Uploading media files to the datalayer through obstore."""

from typing import TYPE_CHECKING

import obstore
from obstore.store import S3Store

from rekuest.client.upload.errors import UploadError
from rekuest.datalayer import DataLayer
from rekuest.scalars import MediaLike


if TYPE_CHECKING:
    from rekuest.api.schema import MediaUploadGrant


def create_s3_store(
    endpoint_url: str, credentials: "MediaUploadGrant", proxy: str | None = None
) -> S3Store:
    """An S3 store for the bucket a grant writes into.

    Path-style requests, because the datalayer is addressed by host (MinIO behind
    the deployment's gateway), not by bucket subdomain. ``http://`` endpoints have to
    be allowed explicitly, and ``proxy`` is the forward proxy a datalayer that is
    only reachable through the mesh is reached through.
    """
    client_options: dict[str, object] = {}
    if endpoint_url.startswith("http://"):
        client_options["allow_http"] = True
    if proxy:
        client_options["proxy_url"] = proxy

    store_kwargs: dict[str, object] = {
        "access_key_id": credentials.access_key,
        "secret_access_key": credentials.secret_key,
        "endpoint": endpoint_url,
        "virtual_hosted_style_request": False,
        "client_options": client_options or None,
    }
    if credentials.session_token:
        store_kwargs["session_token"] = credentials.session_token

    return S3Store(credentials.bucket, **store_kwargs)


async def astore_media_file(
    file: MediaLike,
    credentials: "MediaUploadGrant",
    datalayer: DataLayer,
) -> str:
    """Put a media file at the key its grant names, and return the grant's store."""
    endpoint_url = await datalayer.get_endpoint_url()
    store = create_s3_store(endpoint_url, credentials, proxy=datalayer.proxy)

    try:
        await obstore.put_async(store, credentials.key, file.value)
    except Exception as e:
        raise UploadError(
            f"Error while uploading to s3://{credentials.bucket}/{credentials.key} on {endpoint_url}"
        ) from e

    return credentials.store
