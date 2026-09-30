"""Shim of the player runtime class ``base.spider.Spider``.

Only the surface that real crawled sources actually touch is implemented.
Empirically (survey of the sources in this repository) a source needs:

* ``self.fetch(url, params=..., headers=..., ...)`` -> a ``requests.Response``
  like object (sources use ``.text`` and ``.json()``)
* ``self.getProxy(url)`` / ``self.getProxyUrl(url)`` -> passthrough

Everything else (``init`` / ``homeContent`` / ...) is the player interface the
sources override themselves.

Set the env var ``TVBOX_SHIM_PROXY`` (e.g. ``socks5://127.0.0.1:1080``) to route
probe traffic through a proxy.  Requires ``requests[socks]`` for socks proxies.
"""

from __future__ import annotations

import os
import sys

import requests

__all__ = ["Spider", "DEFAULT_UA", "PLAYER_UA"]

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
PLAYER_UA = (
    "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)


def _build_session() -> requests.Session:
    session = requests.Session()
    proxy = os.environ.get("TVBOX_SHIM_PROXY")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    session.trust_env = True
    return session


class Spider:
    """Drop-in for the player's base spider."""

    headers = {"User-Agent": DEFAULT_UA}
    timeout = 15

    def __init__(self, *args, **kwargs):  # sources rarely call super().__init__
        self._shim_session = _build_session()
        self._shim_cache = {}

    # ------------------------------------------------------------------ network
    def fetch(
        self,
        url,
        params=None,
        headers=None,
        timeout=None,
        encoding=None,
        method="GET",
        data=None,
        json=None,
        allow_redirects=True,
        verify=False,
        **kwargs,
    ):
        merged = dict(self.headers or {})
        if headers:
            merged.update({k: v for k, v in headers.items() if v is not None})
        response = self._shim_session.request(
            str(method).upper(),
            url,
            params=params,
            headers=merged,
            data=data,
            json=json,
            timeout=timeout or self.timeout,
            allow_redirects=allow_redirects,
            verify=verify,
            **kwargs,
        )
        if encoding:
            response.encoding = encoding
        return response

    def post(self, url, data=None, headers=None, timeout=None, **kwargs):
        return self.fetch(
            url, data=data, headers=headers, timeout=timeout, method="POST", **kwargs
        )

    # ------------------------------------------------------------------- proxy
    def getProxy(self, url=None):
        return url

    def getProxyUrl(self, url=None):
        return url

    # ------------------------------------------------------------ cache / log
    # The real player runtime exposes these too; some sources rely on them.
    def log(self, *args):
        print("[spider]", *args, file=sys.stderr)

    def getCache(self, key):
        return self._shim_cache.get(key)

    def setCache(self, key, value, expire=None):
        self._shim_cache[key] = value
        return True

    def delCache(self, key):
        self._shim_cache.pop(key, None)
        return True

    # -------------------------------------------------------- player interface
    def init(self, extend=""):
        return None

    def destroy(self):
        return None

    def getName(self):
        return "shim"

    def homeContent(self, filter=False):
        return {"class": []}

    def homeVideoContent(self):
        return {"list": []}

    def categoryContent(self, tid, pg=1, filter=False, extend=None):
        return {"list": []}

    def detailContent(self, ids):
        return {"list": []}

    def searchContent(self, key, quick=False, pg="1"):
        return {"list": []}

    def playerContent(self, flag, id, vipFlags=None):
        return {}

    def localProxy(self, param):
        return None
