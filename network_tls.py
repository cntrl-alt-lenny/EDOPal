"""TLS trust shared by artwork and tournament downloads."""

import os
import ssl

import aiohttp
import certifi


def create_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    for variable in ("EDOPRO_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        path = os.path.expanduser(os.environ.get(variable, ""))
        if path and os.path.isfile(path):
            context.load_verify_locations(cafile=os.path.abspath(path))
            break
    return context


def create_http_session(
    *, limit: int | None = None, limit_per_host: int | None = None
) -> aiohttp.ClientSession:
    """Create an HTTPS session that honors standard proxy and CA-bundle settings."""
    connector_kwargs: dict[str, object] = {"ssl": create_ssl_context()}
    if limit is not None:
        connector_kwargs["limit"] = limit
    if limit_per_host is not None:
        connector_kwargs["limit_per_host"] = limit_per_host
    connector = aiohttp.TCPConnector(**connector_kwargs)
    return aiohttp.ClientSession(connector=connector, trust_env=True)
