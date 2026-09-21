"""The base graphql client for rekuest"""

from rath import rath


class RekuestRath(rath.Rath):
    """A Rath client for Rekuest.

    This class is a wrapper around the Rath client and provides
    a default composition of links for Rekuest, that allows
    for authentication, retrying, and shrinking of requests.

    Entering it does not make it "the current client". Only the rekuest service
    that owns it is current while entered (see :class:`rekuest.client.client.Rekuest`);
    a client used on its own is passed where it is needed, as ``rath=``.
    """
