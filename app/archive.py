# -*- coding: utf-8 -*-
"""git 에 얹기 좋은 이력 보관소."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from .collector import MeasuredKeyword

FIELDS = ("collected_at", "keyword", "target", "rank", "total_ads", "unstable", "samples", "source")


def to_rows(measurements: Sequence[MeasuredKeyword], source: str) -> list[dict]:
    """수집 결과를 저장소/분석이 공통으로 쓰는 평평한 행으로 편다."""
    return [
        {
            "collected_at": m.fetched_at.isoformat(timespec="seconds"),
            "keyword": m.keyword,
            "target": tr.name,
            "rank": tr.rank,
            "total_ads": m.total_ads,
            "unstable": 1 if tr.unstable else 0,
            "samples": ",".join("-" if s is None else str(s) for s in tr.samples),
            "source": source,
        }
        for m in measurements
        for tr in m.ranks
    ]


def append(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a", encoding="utf-8", newline="\n") as fp:
        for row in rows:
            fp.write(json.dumps({k: row.get(k) for k in FIELDS}, ensure_ascii=False) + "\n")
            written += 1
    return written


def load(path: Path, hours: int | None = None) -> list[dict]:
    """이력을 읽는다. 깨진 줄은 조용히 건너뛴다 — 한 줄 때문에 리포트가 죽으면 안 된다."""
    if not path.exists():
        return []
    cutoff = (
        (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        if hours
        else None
    )
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cutoff and row.get("collected_at", "") < cutoff:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.get("collected_at", ""))
    return rows


def prune(path: Path, keep_days: int) -> int:
    """오래된 줄을 잘라낸다. 파일이 무한정 커지는 걸 막는다."""
    rows = load(path)
    cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat(timespec="seconds")
    kept = [r for r in rows if r.get("collected_at", "") >= cutoff]
    if len(kept) == len(rows):
        return 0
    path.write_text(
        "".join(json.dumps({k: r.get(k) for k in FIELDS}, ensure_ascii=False) + "\n" for r in kept),
        encoding="utf-8",
        newline="\n",
    )
    return len(rows) - len(kept)
