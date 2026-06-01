from __future__ import annotations

import json
import random
import re
from html import escape
from pathlib import Path
from typing import Iterable, Sequence

COLOR_MIN = 50
COLOR_MAX = 200
SEQ_PATTERN = re.compile(r"seq length\s*=\s*(\d+)", re.IGNORECASE)


def random_color() -> str:
    return f"rgb({random.randint(COLOR_MIN, COLOR_MAX)}, {random.randint(COLOR_MIN, COLOR_MAX)}, {random.randint(COLOR_MIN, COLOR_MAX)})"


def parse_seq_length(log_path: Path) -> int:
    if not log_path.exists():
        raise FileNotFoundError(log_path)
    with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = SEQ_PATTERN.search(line)
            if match:
                return int(match.group(1))
    raise ValueError("로그 파일에서 'seq length = NNN' 패턴을 찾을 수 없습니다.")


def _legend_from_region(region_index: str | int | None) -> str:
    if region_index is None:
        return "Unknown"
    text = str(region_index).strip()
    if not text:
        return "Unknown"
    return f"Phage {text}" if text.isdigit() else text


def convert_to_cgview(sample_dir: Path, sample_label: str | None) -> Path | None:
    json_path = sample_dir / "json_input"
    log_path = _locate_log(sample_dir, sample_label)
    if not json_path.exists() or not log_path:
        return None

    with json_path.open("r", encoding="utf-8") as handle:
        features_raw: Sequence[dict] = json.load(handle)
    seq_length = parse_seq_length(log_path)

    features: list[dict] = []
    legends: set[str] = set()
    for item in features_raw:
        start = int(item.get("start", 0))
        stop = int(item.get("stop", 0))
        if stop <= 0 or stop < start:
            continue
        strand_val = 1 if (item.get("strand") or "+") == "+" else -1
        legend_val = _legend_from_region(item.get("region_index"))
        legends.add(legend_val)
        features.append(
            {
                "type": item.get("type") or "Unknown",
                "name": item.get("name") or "Unknown",
                "start": start,
                "stop": stop,
                "strand": strand_val,
                "source": (item.get("phage_bac_class") or "Unknown").capitalize(),
                "legend": legend_val,
            }
        )

    if not features:
        return None

    legend_items = [
        {"name": legend, "swatchColor": random_color(), "decoration": "arrow"} for legend in sorted(legends)
    ]

    cgview_json = {
        "cgview": {
            "version": "1.7.0",
            "sequence": {"length": seq_length},
            "features": features,
            "legend": {"items": legend_items},
            "tracks": [
                {
                    "name": "Phage",
                    "dataType": "feature",
                    "dataMethod": "source",
                    "dataKeys": "Phage",
                }
            ],
        }
    }

    label = _sanitize_label(sample_label) or sample_dir.name
    output_path = sample_dir.parent / f"{label}_output_for_viz.json"
    output_path.write_text(json.dumps(cgview_json, indent=2), encoding="utf-8")
    return output_path


def render_html_report(sample_dir: Path, sample_label: str | None) -> Path | None:
    summary_path = sample_dir / "summary.txt"
    detail_path = sample_dir / "detail.txt"
    if not summary_path.exists() and not detail_path.exists():
        return None

    sections: list[str] = []
    if summary_path.exists():
        summary_text = summary_path.read_text(encoding="utf-8", errors="ignore")
        sections.append(_render_pre_block("Summary", summary_text))
    if detail_path.exists():
        detail_text = detail_path.read_text(encoding="utf-8", errors="ignore")
        sections.append(_render_pre_block("Detail", detail_text))

    label = _sanitize_label(sample_label) or sample_dir.name
    html = f"""<!DOCTYPE html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <title>PHASTEST Report · {escape(label)}</title>
    <style>
      body {{
        font-family: "Pretendard", "Noto Sans KR", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        margin: 24px;
        background: #f8fafc;
        color: #0f172a;
      }}
      h1 {{
        font-size: 1.5rem;
        margin-bottom: 1rem;
      }}
      section {{
        background: #fff;
        border-radius: 16px;
        padding: 16px;
        margin-bottom: 24px;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
        border: 1px solid #e2e8f0;
      }}
      h2 {{
        font-size: 1rem;
        margin-top: 0;
      }}
      pre {{
        white-space: pre-wrap;
        font-size: 0.85rem;
        background: #0f172a;
        color: #e2e8f0;
        padding: 12px;
        border-radius: 12px;
        overflow-x: auto;
      }}
    </style>
  </head>
  <body>
    <h1>PHASTEST Report · {escape(label)}</h1>
    {''.join(sections)}
  </body>
</html>
"""
    report_path = sample_dir.parent / f"{label}_report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path


def _render_pre_block(title: str, content: str) -> str:
    return f"<section><h2>{escape(title)}</h2><pre>{escape(content.strip())}</pre></section>"


def _sanitize_label(label: str | None) -> str | None:
    if not label:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip())
    return safe or None


def _locate_log(sample_dir: Path, sample_label: str | None) -> Path | None:
    candidates: Iterable[Path] = []
    if sample_label:
        candidates = [
            sample_dir / f"{sample_label}.log",
            sample_dir / f"{_sanitize_label(sample_label)}.log",
            sample_dir / f"{sample_dir.name}.log",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
    logs = sorted(sample_dir.glob("*.log"))
    return logs[0] if logs else None
