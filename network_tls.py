"""TLS trust shared by artwork and tournament downloads."""

import os
import ssl

import certifi


def create_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    for variable in ("EDOPRO_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        path = os.path.expanduser(os.environ.get(variable, ""))
        if path and os.path.isfile(path):
            context.load_verify_locations(cafile=os.path.abspath(path))
            break
    return context
