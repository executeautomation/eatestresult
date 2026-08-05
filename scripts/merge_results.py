#!/usr/bin/env python3
"""Merge a re-run of individual tests back into a full results.json.

Usage:
  python3 scripts/merge_results.py --into output/ea-regression/results.json \
      --from /tmp/fix1/results.json [--copy-evidence]

Re-run entries replace same-id entries; totals are recomputed from the merged set.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--into", required=True)
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--copy-evidence", action="store_true")
    args = ap.parse_args()

    dst_path, src_path = Path(args.into), Path(args.src)
    dst = json.loads(dst_path.read_text())
    src = json.loads(src_path.read_text())
    patch = {r["id"]: r for r in src["results"]}

    for i, r in enumerate(dst["results"]):
        if r["id"] in patch:
            new = patch.pop(r["id"])
            if args.copy_evidence:
                for rel in new.get("screenshots", []) + ([new["video"]] if new.get("video") else []):
                    s, d = src_path.parent / rel, dst_path.parent / rel
                    if s.exists():
                        d.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(s, d)
            # a re-run that now passes must not keep the old failure evidence
            for rel in r.get("screenshots", []) + ([r["video"]] if r.get("video") else []):
                old = dst_path.parent / rel
                if old.exists() and rel not in (new.get("screenshots") or []):
                    old.unlink()
            dst["results"][i] = new
            print(f"merged {new['id']}: {r['status']} -> {new['status']}")
    for leftover in patch.values():
        dst["results"].append(leftover)
        print(f"appended {leftover['id']} ({leftover['status']})")

    rs = dst["results"]
    dst.update({
        "total": len(rs),
        "passed": sum(r["status"] == "pass" for r in rs),
        "failed": sum(r["status"] == "fail" for r in rs),
        "errors": sum(r["status"] == "error" for r in rs),
        "skipped": sum(r["status"] == "skipped" for r in rs),
        "merged_utc": datetime.now(timezone.utc).isoformat(),
    })
    dst_path.write_text(json.dumps(dst, indent=2))
    print(f"{dst['passed']}/{dst['total']} passing, {dst['failed']} failed, {dst['errors']} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
