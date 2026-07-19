#!/usr/bin/env python3
"""Render deterministic local SVG charts from anonymized observation JSON."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "benchmarks" / "observations.json").read_text(encoding="utf-8"))
OUT = ROOT / "docs" / "assets"

def svg(title: str, rows: list[tuple[str, int]], limit: int) -> str:
    height = 70 + len(rows) * 34
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="760" height="{height}" viewBox="0 0 760 {height}" role="img" aria-labelledby="title desc"><title id="title">{title}</title><desc id="desc">Observed proxy data. Not token billing.</desc><rect width="100%" height="100%" fill="#0b1020"/><text x="24" y="32" fill="#f8fafc" font-family="Arial" font-size="18" font-weight="700">{title}</text><text x="24" y="53" fill="#94a3b8" font-family="Arial" font-size="12">Observed/proxy data — not token billing</text>']
    for index, (label, value) in enumerate(rows):
        y = 80 + index * 34; width = round(430 * value / max(1, limit))
        parts += [f'<text x="24" y="{y + 15}" fill="#cbd5e1" font-family="Arial" font-size="13">{label}</text>', f'<rect x="250" y="{y}" width="{width}" height="20" rx="4" fill="#38bdf8"/>', f'<text x="690" y="{y + 15}" fill="#f8fafc" font-family="Arial" font-size="13" text-anchor="end">{value}</text>']
    return "".join(parts) + "</svg>\n"

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    before_after = [("Instruction bytes: before", DATA["instruction_bytes"]["before"]), ("Instruction bytes: after", DATA["instruction_bytes"]["after"])]
    (OUT / "instruction-bytes.svg").write_text(svg("Instruction size (observed proxy)", before_after, DATA["instruction_bytes"]["before"]), encoding="utf-8")
    starts = list(DATA["observed_starts"].items())
    (OUT / "observed-starts.svg").write_text(svg("Observed starts by work type", starts, max(value for _, value in starts)), encoding="utf-8")

if __name__ == "__main__": main()
