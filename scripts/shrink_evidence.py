#!/usr/bin/env python3
"""Shrink failure evidence so each file's base64 payload fits in one CLI argument.

PNG screenshots -> JPEG (progressively lower quality/width), MP4 -> lower fps/width/CRF.
Rewrites the report's links in place to the shrunk filenames.

Usage: python3 scripts/shrink_evidence.py --dir output/ea-regression [--budget 88000]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from PIL import Image

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def shrink_image(src: Path, budget: int) -> Path:
    dest = src.with_suffix(".jpg")
    im = Image.open(src).convert("RGB")
    for width, q in ((1280, 72), (1100, 65), (960, 60), (820, 52), (700, 45), (600, 40)):
        w = min(width, im.width)
        r = im.resize((w, max(1, round(im.height * w / im.width))), Image.LANCZOS)
        r.save(dest, "JPEG", quality=q, optimize=True, progressive=True)
        if dest.stat().st_size <= budget:
            return dest
    # A full-page shot of a very long listing stays huge at any quality: keep the top of the
    # page (where the evidence is) rather than shipping an unreadable thumbnail.
    for keep, q in ((1600, 62), (1200, 58), (900, 52)):
        r = Image.open(dest)
        r.crop((0, 0, r.width, min(r.height, keep))).save(
            dest, "JPEG", quality=q, optimize=True, progressive=True)
        if dest.stat().st_size <= budget:
            break
    return dest


def shrink_video(src: Path, budget: int) -> Path:
    dest = src.with_name(src.stem + "-small.mp4")
    for width, fps, crf in ((960, 8, 34), (854, 6, 36), (720, 5, 38), (640, 4, 40), (480, 4, 42)):
        subprocess.run(
            [FFMPEG, "-y", "-i", str(src), "-vf",
             f"fps={fps},scale={width}:trunc(ow/a/2)*2", "-c:v", "libx264", "-preset", "veryslow",
             "-crf", str(crf), "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(dest)],
            capture_output=True, text=True, check=False,
        )
        if dest.exists() and 0 < dest.stat().st_size <= budget:
            break
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--budget", type=int, default=88000, help="max bytes per file (base64 adds ~33%)")
    args = ap.parse_args()

    d = Path(args.dir)
    renames: dict[str, str] = {}

    for png in sorted((d / "screenshots").glob("*.png")):
        jpg = shrink_image(png, args.budget)
        renames[f"screenshots/{png.name}"] = f"screenshots/{jpg.name}"
        print(f"{png.name}: {png.stat().st_size} -> {jpg.name} {jpg.stat().st_size}")
        png.unlink()

    for mp4 in sorted((d / "videos").glob("*.mp4")):
        if mp4.stem.endswith("-small"):
            continue
        small = shrink_video(mp4, args.budget)
        if small.exists() and small.stat().st_size < mp4.stat().st_size:
            renames[f"videos/{mp4.name}"] = f"videos/{small.name}"
            print(f"{mp4.name}: {mp4.stat().st_size} -> {small.name} {small.stat().st_size}")
            mp4.unlink()
        else:
            print(f"{mp4.name}: kept ({mp4.stat().st_size})")

    report = d / "REPORT.md"
    if report.exists() and renames:
        txt = report.read_text()
        for old, new in renames.items():
            txt = txt.replace(old, new)
        report.write_text(txt)
        print(f"rewrote {len(renames)} links in {report.name}")

    oversize = [p for p in d.rglob("*") if p.is_file() and p.stat().st_size > args.budget]
    for p in oversize:
        print(f"STILL OVER BUDGET: {p.relative_to(d)} = {p.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
