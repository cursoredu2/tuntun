#!/usr/bin/env python3
"""Turn the probed source list into a player interface JSON (a "仓").

Reads ``tools/probe.json`` (from ``probe_all.py``) plus a small persistent
``state.json`` so a source is only dropped after several *consecutive* failures.
That keeps a flaky network from deleting half the list on one bad day, while a
genuinely dead source still disappears after a couple of runs.

Usage:
    python tools/gen_sources.py --py-dir py --probe tools/probe.json \
        --repo owner/name --ref main --out tuntun.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.parse

DEFAULT_PREFIX = "https://ghfast.top/"


def load_json(path: str, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print("WARN: cannot read %s (%s)" % (path, exc), file=sys.stderr)
        return default


def collect_sources(py_dir: str) -> list:
    found = []
    for root, dirs, files in os.walk(py_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(files):
            if name.lower().endswith(".py") and not name.startswith("_"):
                found.append(name)
    return sorted(set(found))


def site_key(name: str) -> str:
    return "py_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]


def build_api(name: str, args) -> str:
    if args.api_mode == "local":
        return "./py/" + name
    raw = "https://raw.githubusercontent.com/%s/%s/py/%s" % (
        args.repo,
        args.ref,
        urllib.parse.quote(name),
    )
    return (args.prefix or "") + raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--py-dir", default="py")
    parser.add_argument("--probe", default="tools/probe.json")
    parser.add_argument("--state", default="tools/state.json")
    parser.add_argument("--out", default="tuntun.json")
    parser.add_argument("--also-all", default="", help="also emit every source, unfiltered")
    parser.add_argument("--report", default="", help="markdown report path")
    parser.add_argument("--repo", default="", help="owner/name used for remote api urls")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX,
                        help="proxy prefix for raw.githubusercontent.com")
    parser.add_argument("--api-mode", choices=("remote", "local"), default="remote")
    parser.add_argument("--min-failures", type=int, default=2,
                        help="consecutive probe failures before a source is dropped")
    parser.add_argument("--max-seconds", type=float, default=1.0,
                        help="drop sources whose listing call took longer than this "
                             "(0 disables the speed check)")
    parser.add_argument("--logo", default="")
    args = parser.parse_args()

    if args.api_mode == "remote" and not args.repo:
        print("ERROR: --repo owner/name is required for --api-mode remote", file=sys.stderr)
        return 2

    names = collect_sources(args.py_dir)
    if not names:
        print("ERROR: no .py sources under %s" % args.py_dir, file=sys.stderr)
        return 2

    probe = load_json(args.probe, {})
    probed = {}
    for item in probe.get("results", []) if isinstance(probe, dict) else []:
        if item.get("file"):
            probed[item["file"]] = item
    probe_available = bool(probed)
    if not probe_available:
        print("WARN: no probe results - keeping every source", file=sys.stderr)

    state = load_json(args.state, {})
    if not isinstance(state, dict):
        state = {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    kept, dropped, slow, unknown = [], [], [], []
    for name in names:
        info = probed.get(name)
        entry = state.setdefault(name, {"fails": 0, "last_ok": "", "last_error": ""})
        if info is None:
            unknown.append(name)
            keep = True
        elif not info.get("ok"):
            entry["fails"] = int(entry.get("fails", 0)) + 1
            entry["last_error"] = str(info.get("error", ""))[:200]
            keep = entry["fails"] < max(1, args.min_failures)
            if not keep:
                dropped.append(name)
        else:
            took = float(info.get("content_elapsed") or 0.0)
            if args.max_seconds and took > args.max_seconds:
                entry["fails"] = int(entry.get("fails", 0)) + 1
                entry["last_error"] = "slow: %.2fs > %.2fs" % (took, args.max_seconds)
                keep = entry["fails"] < max(1, args.min_failures)
                if not keep:
                    slow.append(name)
            else:
                entry["fails"] = 0
                entry["last_ok"] = now
                entry["last_error"] = ""
                keep = True
        if keep:
            kept.append(name)

    kept = sorted(set(kept))
    sites = [
        {
            "key": site_key(name),
            "name": os.path.splitext(name)[0],
            "type": 3,
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 0,
            "api": build_api(name, args),
        }
        for name in kept
    ]
    payload = {"spider": "", "logo": args.logo, "sites": sites}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    if args.also_all:
        all_sites = [
            {
                "key": site_key(name),
                "name": os.path.splitext(name)[0],
                "type": 3,
                "searchable": 1,
                "quickSearch": 1,
                "filterable": 0,
                "api": build_api(name, args),
            }
            for name in names
        ]
        with open(args.also_all, "w", encoding="utf-8") as handle:
            json.dump({"spider": "", "logo": args.logo, "sites": all_sites},
                      handle, ensure_ascii=False, indent=2)

    with open(args.state, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)

    if args.report:
        lines = [
            "# Source probe report",
            "",
            "- generated: %s" % now,
            "- file: `%s`" % args.out,
            "- kept: **%d** / %d" % (len(kept), len(names)),
            "- dropped (failed %d+ runs): %d" % (args.min_failures, len(dropped)),
            "- dropped for slow response (> %.2fs): %d" % (args.max_seconds, len(slow)),
            "- not probed: %d" % len(unknown),
            "- probe data: %s" % ("yes" if probe_available else "MISSING (nothing filtered)"),
            "",
            "| source | result | detail |",
            "| --- | --- | --- |",
        ]
        for name in names:
            info = probed.get(name)
            if info is None:
                lines.append("| %s | ? not probed | |" % name)
            elif info.get("ok"):
                took = float(info.get("content_elapsed") or 0.0)
                flag = " (slow)" if args.max_seconds and took > args.max_seconds else ""
                lines.append("| %s | ok%s | %s / %s items / %.2fs |"
                             % (name, flag, info.get("stage", ""),
                                info.get("items", 0), took))
            else:
                fails = state.get(name, {}).get("fails", 0)
                lines.append("| %s | fail x%s | %s |"
                             % (name, fails, str(info.get("error", "")).replace("|", "/")[:120]))
        os.makedirs(os.path.dirname(os.path.abspath(args.report)) or ".", exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    print("kept=%d dropped=%d slow=%d notProbed=%d -> %s"
          % (len(kept), len(dropped), len(slow), len(unknown), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
