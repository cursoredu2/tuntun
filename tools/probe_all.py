#!/usr/bin/env python3
"""Probe every TVBox source under a directory, in parallel, into one JSON file.

Each source runs in its own subprocess (see ``probe_one.py``) with a hard
timeout, so one bad source cannot take the whole run down.

Usage:
    python tools/probe_all.py --py-dir py --out tools/probe.json \
        --concurrency 12 --timeout 25
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

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE_ONE = os.path.join(HERE, "probe_one.py")
SHIM_DIR = os.path.join(HERE, "tvbox_shim")


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
            cwd=os.path.dirname(os.path.abspath(path)) or ".",
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
        }

    stdout = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    for line in reversed(stdout):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                payload.setdefault("elapsed", round(time.time() - started, 2))
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
    args = parser.parse_args()

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
        "py_dir": args.py_dir,
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
        print("WARNING: every probe failed - check network access before trusting this", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
