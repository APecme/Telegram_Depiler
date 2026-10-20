"""HTTP requests using the configured proxy without patching global sockets."""
from __future__ import annotations

import http.client
import os
from urllib.parse import quote, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from .config import get_settings


def open_url(request, timeout=20):
    settings = get_settings()
    host, port = settings.proxy_host, settings.proxy_port
    if not host or not port:
        return build_opener().open(request, timeout=timeout)
    host = host.strip()
    host = urlsplit(host if "://" in host else "//" + host).hostname or host
    if host in {"127.0.0.1", "localhost", "::1"} and (os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER") == "true"):
        host = "host.docker.internal"
    kind = settings.proxy_type or "http"
    if kind == "http":
        credentials = ""
        if settings.proxy_user:
            credentials = quote(settings.proxy_user, safe="") + ":" + quote(settings.proxy_password or "", safe="") + "@"
        address = f"http://{credentials}{'[' + host + ']' if ':' in host else host}:{port}"
        return build_opener(ProxyHandler({"http": address, "https": address})).open(request, timeout=timeout)

    import socks

    class Connection(http.client.HTTPSConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = lambda address, timeout=None, source_address=None: socks.create_connection(
                address, timeout=timeout, source_address=source_address,
                proxy_type=socks.SOCKS5 if kind == "socks5" else socks.SOCKS4,
                proxy_addr=host, proxy_port=port, proxy_rdns=True,
                proxy_username=settings.proxy_user, proxy_password=settings.proxy_password,
            )

    class Handler(HTTPSHandler):
        def https_open(self, req):
            return self.do_open(Connection, req, context=self._context)

    return build_opener(ProxyHandler({}), Handler()).open(request, timeout=timeout)
