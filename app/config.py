# -*- coding: utf-8 -*-
"""config.json 읽기/쓰기.

설정의 단일 출처는 프로젝트 루트의 config.json 이다.
기본 키워드 31개와 기준 업체가 여기 들어 있고, 메모장으로 직접 고쳐도 된다.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .collector import Target

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
HISTORY_PATH = ROOT / "data" / "history.jsonl"

# 요구사항 문서의 31개 키워드. 표 순서를 그대로 지킨다 —
# 담당자가 쓰는 리포트와 행 순서가 어긋나면 대조가 어려워진다.
DEFAULT_KEYWORDS = [
    "중고차매매사이트", "내차시세조회", "차량조회", "중고차조회", "중고차가격",
    "자동차시세", "차량가액조회", "중고차판매", "중고차시세조회사이트",
    "중고자동차매매사이트", "캠핑카", "캠핑카중고", "중고차팔기", "내차시세",
    "내차팔기", "중고자동차", "중고차", "중고차사이트", "중고차시세",
    "중고차시세조회", "중고차판매사이트", "차량판매", "중고차어플", "중고차앱",
    "중고차추천", "중고차거래", "중고차매매", "중고차매입", "중고차사이트순위",
    "중고차판매어플", "중고차할부",
]

DEFAULT_CONFIG: dict = {
    "keywords": DEFAULT_KEYWORDS,
    "targets": [
        # 첫 번째가 기준 업체다. 요구사항 문서의 대상은 헤이딜러다.
        {"name": "헤이딜러", "patterns": ["헤이딜러", "heydealer"]},
        {"name": "엔카", "patterns": ["엔카", "encar"]},
        # 매칭어는 부분 일치라 느슨하면 남의 광고를 잡는다.
        # '다이렉트카' 하나만 두면 '국민다이렉트카'가 걸려 1위로 둔갑한다.
        {"name": "K다이렉트카", "patterns": ["K다이렉트카", "kdirectcar"]},
        {"name": "현대글로비스오토벨", "patterns": ["오토벨", "autobell"]},
    ],
    "repeat": 3,
    "max_workers": 5,
    "max_pages": 10,
}

_lock = threading.Lock()


@dataclass
class AppConfig:
    keywords: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    repeat: int = 3
    max_workers: int = 5
    max_pages: int = 10

    @property
    def primary_target(self) -> Target | None:
        return self.targets[0] if self.targets else None

    def to_dict(self) -> dict:
        return {
            "keywords": self.keywords,
            "targets": [{"name": t.name, "patterns": list(t.patterns)} for t in self.targets],
            "repeat": self.repeat,
            "max_workers": self.max_workers,
            "max_pages": self.max_pages,
        }


def _coerce(raw: dict) -> AppConfig:
    merged = {**DEFAULT_CONFIG, **(raw or {})}
    targets = [
        Target(name=t["name"], patterns=list(t.get("patterns") or [t["name"]]))
        for t in merged.get("targets", [])
        if t.get("name")
    ]
    return AppConfig(
        keywords=[k.strip() for k in merged.get("keywords", []) if str(k).strip()],
        targets=targets,
        repeat=max(1, min(5, int(merged.get("repeat", 3)))),
        max_workers=max(1, min(10, int(merged.get("max_workers", 5)))),
        max_pages=max(1, min(30, int(merged.get("max_pages", 10)))),
    )


def load() -> AppConfig:
    with _lock:
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return _coerce(DEFAULT_CONFIG)
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 설정 파일이 깨져도 서비스는 떠야 한다.
            raw = {}
        return _coerce(raw)


def save(cfg: AppConfig) -> AppConfig:
    with _lock:
        CONFIG_PATH.write_text(
            json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return cfg


def parse_keywords(text: str) -> list[str]:
    """'중고차팔기, 내차팔기' 또는 줄바꿈/탭 구분 입력을 리스트로 바꾼다.

    엑셀에서 셀 여러 개를 그대로 복사해 붙여넣어도 인식되도록
    콤마·줄바꿈·탭을 모두 구분자로 취급하고 중복은 순서를 지키며 제거한다.
    """
    if not text:
        return []
    parts = [p.strip().strip('"').strip("'") for p in re_split(text)]
    seen: dict[str, None] = {}
    for p in parts:
        if p:
            seen.setdefault(p, None)
    return list(seen)


def re_split(text: str) -> list[str]:
    import re

    return re.split(r"[,\n\r\t;]+", text)
