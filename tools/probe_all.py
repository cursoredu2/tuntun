#!/usr/bin/env python3
"""Probe every TVBox source under a directory, in parallel, into one JSON file.

Sources can either be read from a local directory, or fetched straight from an
upstream GitHub repository (``--fetch-upstream``) so the probed list always
matches what upstream currently publishes.

Each source runs in its own subprocess (see ``probe_one.py``) with a hard
timeout, so one bad source cannot take the whole run down.

Usage:
    # local directory
    python tools/probe_all.py --py-dir py --out tools/probe.json

    # straight from the upstream repository
    GITHUB_TOKEN=... python tools/probe_all.py --fetch-upstream ge6bu6/tuntun \
        --py-dir .probe/py --out tools/probe.json
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE_ONE = os.path.join(HERE, "probe_one.py")
SHIM_DIR = os.path.join(HERE, "tvbox_shim")

UA = "tuntun-auto-probe"


def github_headers(token: str = "") -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def fetch_upstream(slug: str, ref: str, work_dir: str, token: str = "",
                   limit: int = 0, workers: int = 8) -> list:
    """Download every ``py/*.py`` from owner/name@ref into work_dir."""
    api = "https://api.github.com/repos/%s/contents/py?ref=%s&per_page=1000" % (slug, ref)
    request = urllib.request.Request(api, headers=github_headers(token))
    with urllib.request.urlopen(request, timeout=60) as response:
        listing = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(listing, list):
        raise RuntimeError("unexpected listing payload: %s" % str(listing)[:200])

    files = [x for x in listing
             if x.get("type") == "file" and str(x.get("name", "")).lower().endswith(".py")]
    if limit:
        files = files[:limit]
    if not files:
        raise RuntimeError("no .py files under %s/py@%s" % (slug, ref))

    os.makedirs(work_dir, exist_ok=True)

    def download(item: dict) -> str:
        dest = os.path.join(work_dir, item["name"])
        # The listing API returns raw, unencoded names which urllib refuses to
        # send, so always rebuild the URL ourselves.
        url = "https://raw.githubusercontent.com/%s/%s/py/%s" % (
            slug, ref, urllib.parse.quote(item["name"]),
        )
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as handle:
            handle.write(resp.read())
        return dest

    saved = []
    with futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        jobs = [pool.submit(download, item) for item in files]
        for job in futures.as_completed(jobs):
            try:
                saved.append(job.result())
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                print("WARN: download failed: %s" % exc, file=sys.stderr)
    print("fetched %d/%d files from %s@%s -> %s"
          % (len(saved), len(files), slug, ref, work_dir), flush=True)
    if not saved:
        raise RuntimeError("every download failed")
    return sorted(saved)


def kid_env() -> dict:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = SHIM_DIR + (os.pathsep + existing if existing else "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def run_one(path: str, timeout: int, init_extend: str) -> dict:
    started = time.time()
    path = os.path.abspath(path)
    cmd = [sys.executable, PROBE_ONE, path]
    if init_extend:
        cmd += ["--init-extend", init_extend]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            env=kid_env(),
            cwd=os.path.dirname(path) or ".",
        )
    except subprocess.TimeoutExpired:
        return {
            "file": os.path.basename(path),
            "name": "",
            "ok": False,
            "stage": "timeout",
            "kind": "",
            "items": 0,
            "error": "no result within %ss" % timeout,
            "elapsed": round(time.time() - started, 2),
            "content_elapsed": 0.0,
        }

    stdout = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    for line in reversed(stdout):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                payload.setdefault("elapsed", round(time.time() - started, 2))
                payload.setdefault("content_elapsed", 0.0)
                return payload
            except json.JSONDecodeError:
                continue

    tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return {
        "file": os.path.basename(path),
        "name": "",
        "ok": False,
        "stage": "crash",
        "kind": "",
        "items": 0,
        "error": (" | ".join(tail[-3:]) or "no output from probe")[:300],
        "elapsed": round(time.time() - started, 2),
        "content_elapsed": 0.0,
    }


def collect_sources(py_dir: str, only: str = "") -> list:
    found = []
    wanted = {x.strip() for x in only.split(",") if x.strip()}
    for root, dirs, files in os.walk(py_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(files):
            if not name.lower().endswith(".py"):
                continue
            if name.startswith("_"):
                continue
            if wanted and name not in wanted and os.path.splitext(name)[0] not in wanted:
                continue
            found.append(os.path.join(root, name))
    return sorted(found)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--py-dir", default="py")
    parser.add_argument("--out", default="tools/probe.json")
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default="", help="comma separated file names")
    parser.add_argument("--init-extend", default="")
    parser.add_argument("--fetch-upstream", default="",
                        help="owner/name: download py/ from there instead of using --py-dir")
    parser.add_argument("--upstream-ref", default="main")
    args = parser.parse_args()

    if args.fetch_upstream:
        try:
            sources = fetch_upstream(
                args.fetch_upstream,
                args.upstream_ref,
                args.py_dir,
                token=os.environ.get("GITHUB_TOKEN", ""),
                limit=args.limit,
            )
        except Exception as exc:  # noqa: BLE001
            print("ERROR: cannot fetch upstream: %s" % exc, file=sys.stderr)
            return 3
    else:
        sources = collect_sources(args.py_dir, args.only)
        if args.limit:
            sources = sources[: args.limit]

    if not sources:
        print("no sources found under %s" % args.py_dir, file=sys.stderr)
        return 2

    print("probing %d sources (concurrency=%d, timeout=%ds)"
          % (len(sources), args.concurrency, args.timeout), flush=True)

    results = []
    done = 0
    with futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        jobs = {
            pool.submit(run_one, path, args.timeout, args.init_extend): path
            for path in sources
        }
        for job in futures.as_completed(jobs):
            results.append(job.result())
            done += 1
            if done % 10 == 0 or done == len(sources):
                ok = sum(1 for r in results if r.get("ok"))
                print("  %d/%d  ok=%d" % (done, len(sources), ok), flush=True)

    results.sort(key=lambda r: r.get("file", ""))
    ok_count = sum(1 for r in results if r.get("ok"))
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": args.fetch_upstream or args.py_dir,
        "upstream_ref": args.upstream_ref if args.fetch_upstream else "",
        "total": len(results),
        "ok": ok_count,
        "failed": len(results) - ok_count,
        "results": results,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)

    print("done: ok=%d failed=%d -> %s" % (ok_count, len(results) - ok_count, args.out))
    if ok_count == 0:
        print("WARNING: every probe failed - check network access before trusting this",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
