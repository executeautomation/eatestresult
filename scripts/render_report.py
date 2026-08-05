#!/usr/bin/env python3
"""Render a Markdown test report from an ea_regression results.json.

Usage:
  python3 scripts/render_report.py --results output/ea-regression/results.json \
      [--extra docs/corrections.md] [--title "..."] [--out output/ea-regression/REPORT.md]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Pacific/Auckland")
BADGE = {"pass": "✅ Pass", "fail": "❌ **Fail**", "error": "💥 Error", "skipped": "⏭️ Skipped"}


def local(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(TZ).strftime("%d %b %Y, %H:%M %Z")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--extra", default="")
    ap.add_argument("--title", default="EA Employee App — Automated Test Results")
    args = ap.parse_args()

    d = json.loads(Path(args.results).read_text())
    rs = d["results"]
    out = Path(args.out) if args.out else Path(args.results).with_name("REPORT.md")
    L: list[str] = []

    L.append(f"# {args.title}\n")
    L.append(f"Automated functional regression against **{d['base_url']}** "
             "(ExecuteAutomation Employee App — ASP.NET Core 8 + EF Core + Identity).\n")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Application under test | {d['base_url']} |")
    L.append(f"| Run started | {local(d['started_utc'])} |")
    dur = (datetime.fromisoformat(d["finished_utc"]) - datetime.fromisoformat(d["started_utc"])).total_seconds()
    L.append(f"| Duration | {dur:.0f}s |")
    L.append("| Runner | Playwright + Chromium (headless, 1440×900), one isolated browser context per test |")
    L.append(f"| Tests | **{d['total']}** |")
    L.append(f"| Passed | **{d['passed']}** |")
    L.append(f"| Failed | **{d['failed']}** |")
    L.append(f"| Errors | {d['errors']} |")
    L.append(f"| Skipped | {d['skipped']} |")
    L.append("| Evidence | full-page screenshot at each failure + screen recording of each failing workflow |")
    L.append("")

    fails = [r for r in rs if r["status"] in ("fail", "error")]
    if fails:
        L.append("## Failures at a glance\n")
        L.append("| # | Severity | Test | What happened | Evidence |")
        L.append("|---|---|---|---|---|")
        for r in fails:
            ev = []
            if r.get("video"):
                ev.append(f"[video]({r['video']})")
            ev += [f"[shot {i+1}]({s})" for i, s in enumerate(r["screenshots"])]
            L.append(f"| {r['id']} | {r['severity'] or '—'} | {r['title']} | "
                     f"{r['actual'].replace('|', '\\|')} | {' · '.join(ev) or '—'} |")
        L.append("")

    if args.extra and Path(args.extra).exists():
        L.append(Path(args.extra).read_text().rstrip() + "\n")

    L.append("## Full results\n")
    L.append("| # | Area | Test | Steps | Expected | Actual | Result |")
    L.append("|---|------|------|-------|----------|--------|--------|")
    for r in rs:
        L.append(
            f"| {r['id']} | {r['area']} | {r['title']} | {r['steps']} | {r['expected']} | "
            f"{r['actual'].replace('|', '\\|')} | {BADGE.get(r['status'], r['status'])} |"
        )
    L.append("")

    notes = [(r["id"], n) for r in rs for n in r["notes"] if not n.startswith("Traceback")]
    if notes:
        L.append("## Observations (not test failures)\n")
        for tid, n in notes:
            L.append(f"- **{tid}** — {n}")
        L.append("")

    if fails:
        L.append("## Failure detail\n")
        for r in fails:
            L.append(f"### {r['id']} — {r['title']}  ·  severity {r['severity'] or 'n/a'}\n")
            L.append(f"**Steps:** {r['steps']}\n")
            L.append(f"**Expected:** {r['expected']}\n")
            L.append(f"**Actual:** {r['actual']}\n")
            if r.get("video"):
                L.append(f"**Recording of the failing workflow:** [`{r['video']}`]({r['video']})\n")
            for s in r["screenshots"]:
                L.append(f"![{r['id']} failure]({s})\n")

    L.append("## How to re-run\n")
    L.append("```bash")
    L.append("python3 scripts/ea_regression.py --out output/ea-regression")
    L.append("python3 scripts/render_report.py --results output/ea-regression/results.json")
    L.append("```")
    L.append("")
    L.append("`--only TC06,TC16` runs a subset, `--keep-all-videos` records passing tests too, "
             "`--base-url` points the suite at another deployment. Test data is created with a "
             "timestamped name and deleted by the delete test in the same run.\n")
    L.append(f"<sub>Generated {datetime.now(TZ).strftime('%d %b %Y, %H:%M %Z')} from `results.json`.</sub>")

    out.write_text("\n".join(L))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
