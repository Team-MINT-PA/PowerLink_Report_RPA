# -*- coding: utf-8 -*-
"""네이버 모바일 파워링크 노출순위 수집기.

수집 대상: https://m.ad.search.naver.com/search.naver?where=m_expd&query=<키워드>

브라우저 자동화 없이 순수 HTTP 로 동작한다. 파워링크 목록 HTML 에는
    nclk(this, 'sct.title', '<광고ID>', <절대순위>)
형태로 순위가 그대로 박혀 나오고, `&page=N` 파라미터로 16위 이후 구간을
직접 요청할 수 있기 때문에 '더보기' 클릭을 흉내낼 필요가 없다.
"""
from __future__ import annotations

import html as html_mod
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median_low
from typing import Iterable, Sequence

import requests

BASE_URL = "https://m.ad.search.naver.com/search.naver"
PAGE_SIZE = 15

# 모바일 UA 로 고정한다. PC UA 를 쓰면 응답 마크업이 달라진다.
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

_ITEM_RE = re.compile(
    r'<li[^>]*class="list_item.*?(?=<li[^>]*class="list_item|<!--! //ad-list)', re.S
)
_RANK_RE = re.compile(r"nclk\(this,\s*'sct\.title',\s*'([^']+)',\s*(\d+)\)")
_SITE_RE = re.compile(r'<span class="site">(.*?)</span>', re.S)
_URL_RE = re.compile(r'<span class="url_link">(.*?)</span>', re.S)
# 제목은 tit_area 안쪽만 본다. 하단 '광고집행기간' 라벨도 span.tit 을 쓰기 때문에
# 블록 전체에서 span.tit 을 긁으면 그 라벨이 제목에 섞여 들어온다.
_TIT_AREA_RE = re.compile(r'<div class="tit_area">(.*?)</div>', re.S)
_TIT_RE = re.compile(r'<span class="tit">(.*?)</span>', re.S)
_DESC_RE = re.compile(r'<a[^>]*class="desc"[^>]*>(.*?)</a>', re.S)
_TOTAL_RE = re.compile(r'data-total="(\d+)"')
_TAG_RE = re.compile(r"<[^>]+>")

RANK_OUT = None  # 순위 밖(미노출)


def _text(raw: str) -> str:
    """HTML 조각에서 태그를 걷어내고 순수 텍스트만 남긴다."""
    return html_mod.unescape(_TAG_RE.sub("", raw)).strip()


@dataclass(frozen=True)
class AdItem:
    rank: int
    ad_id: str
    site: str          # 광고주 업체명 (예: 현대글로비스오토벨)
    domain: str        # 표시 도메인 (예: autobell.co.kr)
    title: str
    desc: str

    @property
    def haystack(self) -> str:
        """업체 매칭에 사용할 문자열. 제목/설명은 오탐 위험이 있어 제외한다."""
        return f"{self.site} {self.domain}".lower()


@dataclass
class Target:
    """추적 대상 업체."""

    name: str
    patterns: Sequence[str]

    def matches(self, item: AdItem) -> bool:
        hay = item.haystack
        return any(p.lower() in hay for p in self.patterns)


@dataclass
class KeywordResult:
    keyword: str
    total_ads: int
    items: list[AdItem]
    fetched_at: datetime
    pages_fetched: int = 0
    truncated: bool = False   # 조기 종료로 뒷 구간을 안 본 경우
    error: str | None = None

    def rank_of(self, target: Target) -> int | None:
        for item in self.items:
            if target.matches(item):
                return item.rank
        return RANK_OUT


class CollectorError(RuntimeError):
    pass


class PowerLinkCollector:
    """키워드별 파워링크 목록을 수집한다.

    스레드 세이프하지 않은 requests.Session 을 스레드마다 하나씩 들고 간다.
    """

    def __init__(
        self,
        max_pages: int = 10,
        timeout: float = 15.0,
        retries: int = 2,
        delay_range: tuple[float, float] = (0.25, 0.7),
        max_workers: int = 5,
    ) -> None:
        self.max_pages = max_pages
        self.timeout = timeout
        self.retries = retries
        self.delay_range = delay_range
        self.max_workers = max_workers
        self._local = threading.local()

    # ------------------------------------------------------------------ HTTP

    @property
    def _session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            sess.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "ko-KR,ko;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Referer": "https://m.ad.search.naver.com/",
                }
            )
            self._local.session = sess
        return sess

    def _fetch(self, keyword: str, page: int) -> str:
        params = {"sm": "", "where": "m_expd", "query": keyword}
        if page > 1:
            params["page"] = str(page)

        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._session.get(BASE_URL, params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    resp.encoding = "utf-8"
                    return resp.text
                # 429/5xx 는 잠시 물러섰다 재시도한다.
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = CollectorError(f"HTTP {resp.status_code}")
                else:
                    raise CollectorError(f"HTTP {resp.status_code}")
            except Exception as exc:  # noqa: BLE001 - 네트워크 예외 전반을 재시도 대상으로 본다
                last_exc = exc
            time.sleep(1.5 * (attempt + 1) + random.random())
        raise CollectorError(f"'{keyword}' page {page} 수집 실패: {last_exc}")

    # ---------------------------------------------------------------- 파싱

    @staticmethod
    def parse(page_html: str, page: int) -> tuple[list[AdItem], int | None]:
        """광고 목록 HTML 을 파싱해 (항목들, 전체 광고수) 를 돌려준다."""
        items: list[AdItem] = []
        for offset, block in enumerate(_ITEM_RE.findall(page_html)):
            m = _RANK_RE.search(block)
            if m:
                ad_id, rank = m.group(1), int(m.group(2))
            else:
                # 마크업이 바뀌어 nclk 가 사라진 경우의 안전망:
                # 페이지 내 등장 순서로 절대 순위를 계산한다.
                ad_id, rank = "", (page - 1) * PAGE_SIZE + offset + 1

            site = _text(_SITE_RE.search(block).group(1)) if _SITE_RE.search(block) else ""
            url_m = _URL_RE.search(block)
            domain = _text(url_m.group(1)).rstrip("/") if url_m else ""
            tit_area = _TIT_AREA_RE.search(block)
            title = (
                " ".join(_text(t) for t in _TIT_RE.findall(tit_area.group(1))).strip()
                if tit_area
                else ""
            )
            desc_m = _DESC_RE.search(block)
            desc = _text(desc_m.group(1)) if desc_m else ""

            items.append(AdItem(rank, ad_id, site, domain, title, desc))

        total_m = _TOTAL_RE.search(page_html)
        total = int(total_m.group(1)) if total_m else None
        return items, total

    # -------------------------------------------------------------- 수집기

    def collect_keyword(
        self, keyword: str, targets: Sequence[Target] = ()
    ) -> KeywordResult:
        """키워드 하나의 파워링크 목록을 수집한다.

        추적 대상(targets)이 주어지면 전원을 찾는 즉시 중단한다.
        상위권에 몰려 있는 보통의 경우 1페이지만 읽고 끝나 응답이 매우 빠르다.
        """
        keyword = keyword.strip()
        fetched_at = datetime.now()
        collected: dict[int, AdItem] = {}
        total = 0
        pages = 0
        remaining = {t.name for t in targets}

        try:
            for page in range(1, self.max_pages + 1):
                if page > 1:
                    time.sleep(random.uniform(*self.delay_range))

                page_html = self._fetch(keyword, page)
                items, page_total = self.parse(page_html, page)
                pages = page
                if page_total is not None:
                    total = max(total, page_total)
                if not items:
                    break

                for item in items:
                    collected[item.rank] = item
                for target in targets:
                    if target.name in remaining and any(target.matches(i) for i in items):
                        remaining.discard(target.name)

                highest = max(collected)
                if targets and not remaining:
                    # 대상 전원 확인 → 나머지 페이지는 볼 이유가 없다.
                    return KeywordResult(
                        keyword, total, _sorted(collected), fetched_at, pages,
                        truncated=highest < total,
                    )
                if total and highest >= total:
                    break
                if len(items) < PAGE_SIZE:
                    break
        except CollectorError as exc:
            return KeywordResult(
                keyword, total, _sorted(collected), fetched_at, pages, error=str(exc)
            )

        return KeywordResult(keyword, total, _sorted(collected), fetched_at, pages)

    def collect_many(
        self,
        keywords: Iterable[str],
        targets: Sequence[Target] = (),
        on_done=None,
    ) -> list[KeywordResult]:
        """키워드들을 동시에 수집한다.

        on_done(keyword, result) 은 **끝난 순서대로** 불린다. 진행률 표시용이며,
        결과 목록은 넘긴 키워드 순서를 그대로 지킨다.
        """
        keywords = [k.strip() for k in keywords if k and k.strip()]
        if not keywords:
            return []

        results: list[KeywordResult | None] = [None] * len(keywords)
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(keywords))) as pool:
            futures = {
                pool.submit(self.collect_keyword, kw, targets): i
                for i, kw in enumerate(keywords)
            }
            for fut in as_completed(futures):
                i = futures[fut]
                results[i] = fut.result()
                if on_done:
                    on_done(keywords[i], results[i])
        return [r for r in results if r is not None]


def _sorted(collected: dict[int, AdItem]) -> list[AdItem]:
    return [collected[r] for r in sorted(collected)]


# ---------------------------------------------------------------- 중앙값 집계


@dataclass
class TargetRank:
    name: str
    rank: int | None            # 대표 순위(중앙값). None 이면 순위밖
    samples: list[int | None] = field(default_factory=list)

    @property
    def unstable(self) -> bool:
        """반복 조회 사이에 순위가 흔들렸는지."""
        seen = {s for s in self.samples}
        return len(seen) > 1


@dataclass
class MeasuredKeyword:
    keyword: str
    total_ads: int
    ranks: list[TargetRank]
    fetched_at: datetime
    error: str | None = None
    last_items: list[AdItem] = field(default_factory=list)


def measure(
    collector: PowerLinkCollector,
    keywords: Sequence[str],
    targets: Sequence[Target],
    repeat: int = 3,
    on_progress=None,
) -> list[MeasuredKeyword]:
    """키워드별로 repeat 회 조회한 뒤 순위 중앙값을 대표값으로 삼는다.

    파워링크는 같은 조건으로 연속 호출해도 광고가 로테이션되어 순위가
    출렁인다(실측: 15개 중 3~4개 변동). 1회성 스냅샷보다 중앙값이
    실무에서 훨씬 안정적이다.
    """
    repeat = max(1, repeat)
    done = [0]
    total = max(1, len(keywords) * repeat)

    def tick(kw, _res):
        done[0] += 1
        if on_progress:
            on_progress(done[0] / total, kw)

    rounds: list[list[KeywordResult]] = [
        collector.collect_many(keywords, targets, on_done=tick) for _ in range(repeat)
    ]

    out: list[MeasuredKeyword] = []
    for idx, keyword in enumerate(keywords):
        per_round = [r[idx] for r in rounds]
        latest = per_round[-1]
        errors = [r.error for r in per_round if r.error]

        target_ranks: list[TargetRank] = []
        for target in targets:
            samples = [r.rank_of(target) for r in per_round]
            found = [s for s in samples if s is not None]
            # 과반이 미노출이면 순위밖으로 본다.
            rank = median_low(found) if len(found) * 2 > len(samples) else RANK_OUT
            target_ranks.append(TargetRank(target.name, rank, samples))

        out.append(
            MeasuredKeyword(
                keyword=keyword,
                total_ads=max((r.total_ads for r in per_round), default=0),
                ranks=target_ranks,
                fetched_at=latest.fetched_at,
                error=errors[0] if errors and len(errors) == len(per_round) else None,
                last_items=latest.items,
            )
        )
    return out
