"""Web-layer middleware that lives outside ``observability``.

Currently houses :mod:`security_headers`. Observability middleware
(request-id, logging, metrics) stays under
``lite_horse.observability.middleware`` so it can be shared with non-web
process types.
"""
from __future__ import annotations

from lite_horse.web.middleware.security_headers import (
    SecurityHeadersMiddleware,
    install_security_headers,
)

__all__ = ["SecurityHeadersMiddleware", "install_security_headers"]
