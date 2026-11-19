"""Uploading media files to the datalayer through obstore."""

from typing import TYPE_CHECKING

import obstore
from obstore.store import S3Store

from rekuest.client.upload.errors import UploadError
from rekuest.datalayer import DataLayer
from rekuest.scalars import MediaLike


if TYPE_CHECKING:
    from rekuest.api.schema import AssetUploadGrant, MediaUploadGrant


def create_s3_store(
    endpoint_url: str,
    credentials: "MediaUploadGrant | AssetUploadGrant",
    proxy: str | None = None,
    certificates: list[str] | None = None,
) -> S3Store:
    """An S3 store for the bucket a grant writes into.

    Path-style requests, because the datalayer is addressed by host (MinIO behind
    the deployment's gateway), not by bucket subdomain. ``http://`` endpoints have to
    be allowed explicitly, and ``proxy`` is the forward proxy a datalayer that is
    only reachable through the mesh is reached through.

    ``certificates`` are a pinned datalayer's hub certificates: the store trusts them and
    nothing else. Not through ``SSL_CERT_FILE``, which obstore >= 0.11 never reads on macOS
    or Windows (it verifies with the platform's verifier there); with the system roots
    disabled it checks against these alone with rustls's own verifier.
    """
    client_options: dict[str, object] = {}
    if endpoint_url.startswith("http://"):
        client_options["allow_http"] = True
    if proxy:
        client_options["proxy_url"] = proxy
    if certificates:
        client_options["root_certificate"] = "\n".join(pem.strip() for pem in certificates)
        client_options["disable_system_certificates"] = True

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
    credentials: "MediaUploadGrant | AssetUploadGrant",
    datalayer: DataLayer,
) -> str:
    """Put a media file at the key its grant names, and return the grant's store."""
    endpoint_url = await datalayer.get_endpoint_url()
    store = create_s3_store(endpoint_url, credentials, proxy=datalayer.proxy, certificates=datalayer.certificates)

    try:
        await obstore.put_async(store, credentials.key, file.value)
    except Exception as e:
        raise UploadError(
            f"Error while uploading to s3://{credentials.bucket}/{credentials.key} on {endpoint_url}"
        ) from e

    return credentials.store
