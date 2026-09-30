#!/usr/bin/env python3
"""Probe exactly one TVBox source file and report whether it can list content.

Run as a subprocess so a hanging or crashing source can be killed by the parent,
and so a source that calls ``sys.exit`` / messes with globals cannot poison the
rest of the run.

Usage:
    python tools/probe_one.py path/to/source.py [--init-extend TEXT]

Prints a single line of JSON on stdout.  Diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys
import time
import traceback
sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

HERE = os.path.dirname(os.path.abspath(__file__))
SHIM_DIR = os.path.join(HERE, "tvbox_shim")
if SHIM_DIR not in sys.path:
    sys.path.insert(0, SHIM_DIR)

# Belt and braces: any socket the source opens itself is capped too.
socket.setdefaulttimeout(float(os.environ.get("PROBE_SOCKET_TIMEOUT", "20")))

MAX_ITEMS = 400
SEARCH_KEYWORD = os.environ.get("PROBE_SEARCH_KEYWORD", "电影")


def _load_module(path: str):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise RuntimeError("source file not found: %s" % path)
    name = "probe_target_%d" % int(time.time() * 1000 % 1000000)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot build import spec for %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _find_spider_class(module):
    cls = getattr(module, "Spider", None)
    if isinstance(cls, type):
        return cls
    for value in vars(module).values():
        if isinstance(value, type) and hasattr(value, "homeContent"):
            return value
    raise RuntimeError("no Spider class found")


def _call_init(spider, extend: str) -> None:
    for args in ((extend,), (), (None,)):
        try:
            spider.init(*args)
            return
        except TypeError:
            continue
        except Exception:
            raise


def _extract_items(value):
    """Return (kind, items) for a player payload, or (None, [])."""
    if not isinstance(value, dict):
        return None, []
    for key, kind in (("class", "class"), ("list", "list")):
        items = value.get(key)
        if isinstance(items, (list, tuple)) and len(items) > 0:
            return kind, list(items)
    return None, []


def _try(fn, *args):
    try:
        return fn(*args), ""
    except TypeError as exc:
        return None, "TypeError: %s" % exc
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def probe(path: str, init_extend: str) -> dict:
    result = {
        "file": os.path.basename(path),
        "name": "",
        "ok": False,
        "stage": "",
        "kind": "",
        "items": 0,
        "error": "",
        "elapsed": 0.0,
        "content_elapsed": 0.0,
    }
    started = time.time()
    try:
        module = _load_module(path)
        cls = _find_spider_class(module)
        spider = cls()
        result["name"] = str(getattr(spider, "name", "") or result["file"])
        _call_init(spider, init_extend)

        # 1) homeContent -> categories (often a static list, no network call at all)
        payload, err = _try(spider.homeContent, False)
        kind, items = _extract_items(payload)
        categories = [x for x in (items or []) if isinstance(x, dict)] if kind == "class" else []

        # 2) the real test: ask for an actual video listing (this needs network)
        best_err = err
        if categories:
            tid = ""
            for cat in categories:
                for key in ("type_id", "tid", "type_flag", "id"):
                    if cat.get(key):
                        tid = str(cat[key])
                        break
                if tid:
                    break
            if tid:
                stage_started = time.time()
                payload, err = _try(spider.categoryContent, tid, "1", False, {})
                kind, items = _extract_items(payload)
                if kind:
                    result.update(ok=True, stage="categoryContent", kind=kind, items=len(items),
                                  content_elapsed=round(time.time() - stage_started, 3))
                    return result
                best_err = err or best_err

        # 3) homeVideoContent -> a video list (network)
        if hasattr(spider, "homeVideoContent"):
            stage_started = time.time()
            payload, err = _try(spider.homeVideoContent)
            kind, items = _extract_items(payload)
            if kind:
                result.update(ok=True, stage="homeVideoContent", kind=kind, items=len(items),
                              content_elapsed=round(time.time() - stage_started, 3))
                return result
            best_err = err or best_err

        # 4) searchContent -> last resort for search-only sources (network)
        if hasattr(spider, "searchContent"):
            stage_started = time.time()
            payload, err = _try(spider.searchContent, SEARCH_KEYWORD, False, "1")
            kind, items = _extract_items(payload)
            if kind:
                result.update(ok=True, stage="searchContent", kind=kind, items=len(items),
                              content_elapsed=round(time.time() - stage_started, 3))
                return result
            best_err = err or best_err

        result["stage"] = "no-listing" if categories else "empty"
        result["error"] = (best_err or "no playable listing returned")[:300]
    except Exception as exc:  # noqa: BLE001 - report anything the source throws
        result["stage"] = "exception"
        result["error"] = ("%s: %s" % (type(exc).__name__, exc))[:300]
        traceback.print_exc(file=sys.stderr)
    finally:
        result["elapsed"] = round(time.time() - started, 2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--init-extend", default="")
    args = parser.parse_args()
    outcome = probe(args.source, args.init_extend)
    sys.stdout.write(json.dumps(outcome, ensure_ascii=False) + "\n")
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
