# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/config/tls.py
======================
TLS trust-store bootstrap for the managed-database connections.

Some Python builds — most commonly the python.org macOS framework build —
ship without a populated system CA trust store, so OpenSSL cannot validate
the certificate chains presented by MongoDB Atlas and Neo4j Aura. The
`certifi` package provides Mozilla's CA bundle; pointing OpenSSL at it via
the standard SSL_CERT_FILE / SSL_CERT_DIR environment variables lets every
TLS client in the process (pymongo, the Neo4j driver, redis-py) verify
certificates without any per-driver configuration.

This is the recommended fix for the macOS "unable to get local issuer
certificate" error and is preferable to disabling verification.
"""
from __future__ import annotations

import os


def ensure_secure_ca() -> str | None:
    """Point OpenSSL at certifi's CA bundle for certificate verification.

    Sets SSL_CERT_FILE and SSL_CERT_DIR only if the caller has not already
    set them, so an explicit operator override always wins. Must run before
    the first TLS connection is opened (connections here are created lazily,
    so calling this in a store's constructor is early enough).

    Returns
    -------
    str | None
        The CA bundle path now in effect, or None if certifi is unavailable.
    """
    try:
        import certifi
    except ImportError:
        return None

    ca_path = os.environ.get("SSL_CERT_FILE") or certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", ca_path)
    os.environ.setdefault("SSL_CERT_DIR", os.path.dirname(ca_path))
    return ca_path
