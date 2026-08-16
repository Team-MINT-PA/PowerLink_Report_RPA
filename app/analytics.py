# -*- coding: utf-8 -*-
"""수집된 순위 이력에서 분석 지표를 뽑아낸다.

원본 이력(rank 테이블)만 있으면 아래가 전부 계산된다.
  · KPI      기준 업체의 평균 순위 / 첫 페이지 노출 수 / 상위 3위 / 순위밖 (+ 직전 대비 증감)
  · 현재순위  키워드 × 업체 최신 순위와 직전 수집 대비 변화량
  · 히트맵    키워드 × 업체 순위 매트릭스
  · 시간대별  0~23시 평균 순위 — "몇 시에 순위가 밀리는가"
  · 변동성    표준편차 기준으로 순위가 불안정한 조합
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import pstdev
from typing import Iterable, Sequence

FIRST_PAGE = 15  # 모바일 파워링크 1페이지 노출 개수


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


@dataclass
class Point:
    at: datetime
    keyword: str
    target: str
    rank: int | None
    total_ads: int


def to_points(rows: Iterable[dict]) -> list[Point]:
    out: list[Point] = []
    for r in rows:
        out.append(
            Point(
                at=_parse(r["collected_at"]),
                keyword=r["keyword"],
                target=r["target"],
                rank=r["rank"],
                total_ads=r.get("total_ads") or 0,
            )
        )
    out.sort(key=lambda p: p.at)
    return out


def _series(points: Sequence[Point]) -> dict[tuple[str, str], list[Point]]:
    grouped: dict[tuple[str, str], list[Point]] = defaultdict(list)
    for p in points:
        grouped[(p.keyword, p.target)].append(p)
    return grouped


def _delta(curr: int | None, prev: int | None) -> int | None:
    """순위 변화량. 양수 = 순위 상승(숫자가 작아짐). 한쪽이 순위밖이면 None."""
    if curr is None or prev is None:
        return None
    return prev - curr


# ------------------------------------------------------------------ 현재 순위


def current_table(points: Sequence[Point], targets: Sequence[str]) -> list[dict]:
    """키워드별 최신 순위 + 직전 수집 대비 변화."""
    grouped = _series(points)
    keywords: list[str] = []
    for p in points:
        if p.keyword not in keywords:
            keywords.append(p.keyword)

    rows: list[dict] = []
    for kw in keywords:
        entry: dict = {"keyword": kw, "total_ads": 0, "fetched_at": None, "ranks": {}}
        for tg in targets:
            hist = grouped.get((kw, tg)) or []
            if not hist:
                entry["ranks"][tg] = {"rank": None, "prev": None, "delta": None, "history": []}
                continue
            curr, prev = hist[-1], (hist[-2] if len(hist) > 1 else None)
            entry["ranks"][tg] = {
                "rank": curr.rank,
                "prev": prev.rank if prev else None,
                "delta": _delta(curr.rank, prev.rank if prev else None),
                # 스탯 타일의 스파크라인용 (최근 12포인트)
                "history": [p.rank for p in hist[-12:]],
            }
            entry["total_ads"] = max(entry["total_ads"], curr.total_ads)
            latest = curr.at.isoformat(timespec="seconds")
            entry["fetched_at"] = max(entry["fetched_at"] or latest, latest)
        rows.append(entry)
    return rows


# ------------------------------------------------------------------------ KPI


def kpi(points: Sequence[Point], primary: str) -> dict:
    """기준 업체의 현재 상태 + 직전 수집 대비 증감.

    현재값은 모든 키워드로 내되, **증감은 직전 기록이 있는 키워드만** 놓고 비교한다.
    이번에 처음 조회한 키워드가 한쪽에만 들어가면 평균이 통째로 왜곡되기 때문이다.
    """
    grouped = _series(points)
    curr: list[int | None] = []
    paired: list[tuple[int | None, int | None]] = []

    for (_, tg), hist in grouped.items():
        if tg != primary or not hist:
            continue
        curr.append(hist[-1].rank)
        if len(hist) > 1:
            paired.append((hist[-1].rank, hist[-2].rank))

    def avg(ranks: list[int | None]) -> float | None:
        found = [r for r in ranks if r is not None]
        return round(sum(found) / len(found), 1) if found else None

    cur_avg = avg([c for c, _ in paired])
    prv_avg = avg([p for _, p in paired])

    return {
        "target": primary,
        "keywords": len(curr),
        "compared": len(paired),
        "avg_rank": avg(curr),
        "avg_rank_delta": (
            round(prv_avg - cur_avg, 1) if cur_avg is not None and prv_avg is not None else None
        ),
        "first_page": sum(1 for r in curr if r is not None and r <= FIRST_PAGE),
        "first_page_prev": sum(1 for c, p in paired if p is not None and p <= FIRST_PAGE),
        "first_page_now": sum(1 for c, p in paired if c is not None and c <= FIRST_PAGE),
        "top3": sum(1 for r in curr if r is not None and r <= 3),
        "top3_prev": sum(1 for c, p in paired if p is not None and p <= 3),
        "top3_now": sum(1 for c, p in paired if c is not None and c <= 3),
        "out": sum(1 for r in curr if r is None),
        "out_prev": sum(1 for c, p in paired if p is None),
        "out_now": sum(1 for c, p in paired if c is None),
    }


def avg_rank_series(points: Sequence[Point], primary: str, limit: int = 12) -> list[float]:
    """KPI 타일 스파크라인용 — 수집 회차별 평균 순위.

    한 회차 안에서도 키워드마다 완료 시각이 몇 초씩 다르므로 분 단위로 묶는다.
    """
    buckets: dict[str, list[int]] = defaultdict(list)
    for p in points:
        if p.target == primary and p.rank is not None:
            buckets[p.at.strftime("%Y-%m-%d %H:%M")].append(p.rank)
    return [
        round(sum(v) / len(v), 1) for _, v in sorted(buckets.items())[-limit:]
    ]


# -------------------------------------------------------------------- 히트맵


def heatmap(points: Sequence[Point], targets: Sequence[str]) -> dict:
    """키워드 × 업체 최신 순위 매트릭스. 값이 작을수록(상위) 진하게 칠한다."""
    grouped = _series(points)
    keywords: list[str] = []
    for p in points:
        if p.keyword not in keywords:
            keywords.append(p.keyword)

    cells = [
        [(grouped[(kw, tg)][-1].rank if grouped.get((kw, tg)) else None) for tg in targets]
        for kw in keywords
    ]
    flat = [v for row in cells for v in row if v is not None]
    return {
        "keywords": keywords,
        "targets": list(targets),
        "cells": cells,
        "max": max(flat) if flat else FIRST_PAGE,
    }


# ---------------------------------------------------------------- 시간대 분석


def hourly(points: Sequence[Point], targets: Sequence[str]) -> dict:
    """0~23시 평균 순위.

    문서에서 지적하신 "매시간대마다 순위변동이 생긴다"를 데이터로 답하는 지표.
    순위밖은 평균에서 제외하고, 대신 표본 수를 같이 넘겨 신뢰도를 판단할 수 있게 한다.
    """
    bucket: dict[str, dict[int, list[int]]] = {t: defaultdict(list) for t in targets}
    for p in points:
        if p.target in bucket and p.rank is not None:
            bucket[p.target][p.at.hour].append(p.rank)

    hours = list(range(24))
    series = {
        t: [
            (round(sum(bucket[t][h]) / len(bucket[t][h]), 1) if bucket[t].get(h) else None)
            for h in hours
        ]
        for t in targets
    }
    counts = [sum(len(bucket[t].get(h, [])) for t in targets) for h in hours]

    best = worst = None
    primary = targets[0] if targets else None
    if primary:
        vals = [(h, v) for h, v in zip(hours, series[primary]) if v is not None]
        if vals:
            best = min(vals, key=lambda x: x[1])
            worst = max(vals, key=lambda x: x[1])

    return {
        "hours": hours,
        "series": series,
        "counts": counts,
        "best_hour": {"hour": best[0], "avg": best[1]} if best else None,
        "worst_hour": {"hour": worst[0], "avg": worst[1]} if worst else None,
    }


# ---------------------------------------------------------------- 변동성 분석


def volatility(points: Sequence[Point], targets: Sequence[str], top: int = 8) -> list[dict]:
    """순위가 가장 불안정한 (키워드, 업체) 조합. 표준편차 내림차순."""
    out: list[dict] = []
    for (kw, tg), hist in _series(points).items():
        if tg not in targets:
            continue
        ranks = [p.rank for p in hist if p.rank is not None]
        if len(ranks) < 3:
            continue
        out.append(
            {
                "keyword": kw,
                "target": tg,
                "stdev": round(pstdev(ranks), 2),
                "min": min(ranks),
                "max": max(ranks),
                "samples": len(ranks),
                "out_count": sum(1 for p in hist if p.rank is None),
            }
        )
    out.sort(key=lambda r: r["stdev"], reverse=True)
    return out[:top]


# ------------------------------------------------------------------ 경쟁 구도


def competitive(points: Sequence[Point], targets: Sequence[str], primary: str) -> list[dict]:
    """키워드별로 기준 업체보다 위에 있는 경쟁사를 집계한다."""
    grouped = _series(points)
    keywords: list[str] = []
    for p in points:
        if p.keyword not in keywords:
            keywords.append(p.keyword)

    rows: list[dict] = []
    for kw in keywords:
        mine = grouped.get((kw, primary))
        my_rank = mine[-1].rank if mine else None
        ahead = []
        for tg in targets:
            if tg == primary:
                continue
            hist = grouped.get((kw, tg))
            r = hist[-1].rank if hist else None
            if r is not None and (my_rank is None or r < my_rank):
                ahead.append({"target": tg, "rank": r})
        ahead.sort(key=lambda x: x["rank"])
        rows.append({"keyword": kw, "rank": my_rank, "ahead": ahead})
    return rows


# ----------------------------------------------------------------- 전체 묶음


def build(rows: Iterable[dict], targets: Sequence[str]) -> dict:
    """API/리포트가 그대로 쓰는 분석 결과 한 덩어리."""
    points = to_points(rows)
    targets = list(targets)
    primary = targets[0] if targets else ""
    trend: dict[str, dict[str, list]] = defaultdict(dict)
    for (kw, tg), hist in _series(points).items():
        trend[kw][tg] = [[p.at.isoformat(timespec="seconds"), p.rank] for p in hist]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "targets": targets,
        "primary": primary,
        "first_page": FIRST_PAGE,
        "kpi": kpi(points, primary) if primary else {},
        "kpi_spark": avg_rank_series(points, primary) if primary else [],
        "current": current_table(points, targets),
        "heatmap": heatmap(points, targets),
        "hourly": hourly(points, targets),
        "volatility": volatility(points, targets),
        "competitive": competitive(points, targets, primary) if primary else [],
        "trend": dict(trend),
        "sample_count": len(points),
    }
