#!/usr/bin/env python3
"""Publish a rendered regression report + evidence to a GitHub repo.

Usage:
  python3 scripts/publish_github.py --owner executeautomation --repo eatestresult \
      --dir output/ea-regression --scripts scripts/ea_regression.py,scripts/render_report.py
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

TOOL = "GITHUB_CREATE_OR_UPDATE_FILE_CONTENTS"


def push(owner: str, repo: str, path: str, local: Path, message: str) -> tuple[bool, str]:
    raw = local.read_bytes()
    text_like = local.suffix.lower() in (".md", ".json", ".py", ".txt", ".yml", ".yaml")
    content = raw.decode("utf-8") if text_like else base64.b64encode(raw).decode("ascii")
    data, err = run_composio_tool(TOOL, {                       # noqa: F821 (sandbox builtin)
        "owner": owner, "repo": repo, "path": path,
        "message": message, "content": content,
    })
    if err:
        return False, str(err)[:300]
    url = ((data or {}).get("content") or {}).get("html_url") or ""
    return True, url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dir", required=True, help="report dir containing REPORT.md, results.json, screenshots/, videos/")
    ap.add_argument("--scripts", default="")
    args = ap.parse_args()

    d = Path(args.dir)
    report = d / "REPORT.md"
    if not report.exists():
        print(f"FATAL: {report} not found", file=sys.stderr)
        return 3

    plan: list[tuple[str, Path]] = [("README.md", report), ("results.json", d / "results.json")]
    for sub in ("screenshots", "videos"):
        for f in sorted((d / sub).glob("*")):
            plan.append((f"{sub}/{f.name}", f))
    for s in [p for p in args.scripts.split(",") if p.strip()]:
        sp = Path(s.strip())
        if sp.exists():
            plan.append((f"scripts/{sp.name}", sp))

    summary = json.loads((d / "results.json").read_text())
    msg = (f"Automated regression: {summary['passed']}/{summary['total']} passed, "
           f"{summary['failed']} failed ({summary['base_url']})")

    ok = 0
    for repo_path, local in plan:
        good, info = push(args.owner, args.repo, repo_path, local, msg)
        print(f"{'OK  ' if good else 'FAIL'} {repo_path:52} {info}")
        ok += good
    print(f"\n{ok}/{len(plan)} files pushed to {args.owner}/{args.repo}")
    return 0 if ok == len(plan) else 1


if __name__ == "__main__":
    raise SystemExit(main())
