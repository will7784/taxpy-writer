"""TLS con certificados del sistema; mantiene validación de identidad y cadena."""
import os
import ssl

import truststore


def tls_context() -> ssl.SSLContext:
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cafile = os.environ.get("SSL_CERT_FILE")
    capath = os.environ.get("SSL_CERT_DIR")
    if cafile or capath:
        context.load_verify_locations(cafile=cafile, capath=capath)
    return context
