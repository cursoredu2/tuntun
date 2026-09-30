"""Minimal stand-in for the TVBox player's runtime package ``base``.

The Android players (OK影视 / PeekPro / 影视仓 ...) inject a module named
``base.spider`` into the Python runtime before executing a crawled source.
This package provides just enough of that surface so a source can be imported
and exercised outside the player, for automated smoke tests.
"""

from . import spider  # noqa: F401

__all__ = ["spider"]
