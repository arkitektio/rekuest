"""
This modules provides the datalayer.

Example:

    A simple datalayer that connects to an s3 instance via access_key and secret_key.
    You can define all of the logic within the context manager

    ```python
    from mikro imports Datalayer

    dl = Datalayer(access_key="XXXX", secret_key="XXXX", endpoint_url="s3.amazonaws.com")

    with dl:
        print(df.fs.ls())

    ```

    Async Usage:

    ```python
    from mikrodatalayer import Datalayer

    dl = Datalayer(access_key="XXXX", secret_key="XXXX", endpoint_url="s3.amazonaws.com")

    async with dl:
        print(df.fs.ls())

    ```


"""

import ssl
from types import TracebackType

from fakts import Alias
from koil.composition import KoiledModel




class DataLayer(KoiledModel):
    """Implements a S3 DataLayer

    This will be used to upload and download files from S3.

    Make sure to set the access_key and secret_key and enter the context
    manager to connect to S3 (if authentication is required for the S3 instance
    and to ensure that the context is exited when the context manager is exited
    (for future cleanup purposes on other datalayers).

    Attributes:
        fs (s3fs.S3FileSystem): The filesystem object

    """

    endpoint_url: str = ""
    host: str = ""
    port: int | None = None
    protocol: str = "https"
    proxy: str | None = None
    """The HTTP forward proxy (``http://host:port``) the store is only reachable
    through, when it is reached over the mesh."""

    certificates: list[str] = []
    """The certificate(s) the store's hub registered, when its alias is pinned:
    TLS to it is verified by those alone (``arkitekt_spec.declare.tls``). Empty
    for any other store."""

    def ssl_context(self) -> ssl.SSLContext:
        """The context to reach the store with: its hub's certificate alone when
        pinned, the public roots otherwise."""
        from arkitekt_spec.declare import tls

        return tls.pinned_context(self.certificates) if self.certificates else tls.default_context()


    @classmethod
    def from_alias(cls, alias: "Alias") -> "DataLayer":
        """Point a datalayer at a resolved address.

        Args:
            alias: Where the store is, resolved when the run connected.

        Returns:
            The datalayer, ready to use.
        """
        # rekuest uploads media through obstore (rustls), which takes its roots only from
        # SSL_CERT_FILE: a pinned store's hub certificate is added there.
        if alias.is_pinned():
            from arkitekt_spec.declare import tls

            tls.trust_natively(alias.certificates)
        return cls(
            endpoint_url=alias.to_http_path(),
            host=alias.host,
            port=alias.port,
            protocol="https" if alias.ssl else "http",
            proxy=alias.proxy,
            certificates=list(alias.certificates) if alias.is_pinned() else [],
        )

    async def get_endpoint_url(self):
        return self.endpoint_url

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None
