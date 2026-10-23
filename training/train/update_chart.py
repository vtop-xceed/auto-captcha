#!/usr/bin/env python3
"""Upsert today's row into README.md's Deploys table and regenerate its mermaid
charts (dataset size + test accuracy over the last 12 months).

Usage: python training/update_chart.py <images> <test accuracy % (or - if unknown)>
Run by the model-refresh workflow right before the image is pushed.
"""
import os
import re
import json
import sys
from datetime import date

TRAIN_DIR = os.path.dirname(os.path.abspath(__file__))
README = os.path.join(os.path.dirname(TRAIN_DIR), "README.md")
ROW_RE = re.compile(r"^\| (\d{4}-\d{2}-\d{2}) \| (\d+) \| (\d+|-) \|\s*$", re.M)


def build_block(kind, ordered):
    last = [(d, i, a) for d, (i, a) in ordered[-12:]]
    dates = [d for d, _, _ in last]
    if kind == "images":
        data = [int(i) for _, i, _ in last]
    else:
        data = [int(a) for _, _, a in last if a != "-"]
    title = "Dataset size per deploy" if kind == "images" else "Test accuracy per deploy"
    ymax = str(max(data) * 11 // 10 + 1) if kind == "images" and data else "100"
    series = f"bar {data}"
    return (
        "```mermaid\nxychart-beta\n"
        f'    title "{title}"\n'
        f"    x-axis [{', '.join(dates)}]\n"
        f'    y-axis "{kind}" 0 --> {ymax}\n'
        f"    {series}\n```\n"
    )


def main():
    images = int(sys.argv[1])
    acc = sys.argv[2].strip() if len(sys.argv) > 2 else "-"
    if acc in ("-", ""):  # no test-set accuracy measured; fall back to last val metrics
        try:
            metrics = json.load(
                open(os.path.join(TRAIN_DIR, "model", "metrics.json"))
            )
            acc = str(int(metrics.get("val_exact_match", 0) * 100 + 0.5))
        except Exception:
            acc = "-"
    if acc != "-":
        acc = str(max(0, min(100, int(acc))))
    with open(README) as f:
        text = f.read()

    rows = {m[0]: (int(m[1]), m[2]) for m in ROW_RE.findall(text)}
    rows[date.today().isoformat()] = (images, acc)
    ordered = sorted(rows.items())

    table = "\n".join(f"| {d} | {i} | {a} |" for d, (i, a) in ordered) + "\n"
    text = re.sub(
        r"(?s)(\|---\|\n).*?(<!-- /deploys-table -->)",
        r"\1" + table + r"\2",
        text,
        count=1,
    )
    text = re.sub(
        r"(?s)(<!-- chart:images -->).*?(<!-- /chart:images -->)",
        r"\1\n" + build_block("images", ordered).replace("\\", "\\\\") + r"\2",
        text,
        count=1,
    )
    text = re.sub(
        r"(?s)(<!-- chart:accuracy -->).*?(<!-- /chart:accuracy -->)",
        r"\1\n" + build_block("accuracy", ordered).replace("\\", "\\\\") + r"\2",
        text,
        count=1,
    )
    with open(README, "w") as f:
        f.write(text)
    print(f"deploys row upserted: {date.today().isoformat()} images={images} acc={acc}")


if __name__ == "__main__":
    main()
