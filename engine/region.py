"""지역 분류 조회 (data/regions.csv).

조회 규칙: (시도, 시군구)가 정확히 일치하는 행 → 없으면 (시도, '*') 기본값.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "regions.csv"

# 긴 시도 이름 → regions.csv의 짧은 이름
SIDO_ALIASES = {
    "서울특별시": "서울", "서울시": "서울",
    "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산",
    "세종특별자치시": "세종", "세종시": "세종",
    "경기도": "경기", "강원도": "강원", "강원특별자치도": "강원",
    "충청북도": "충북", "충청남도": "충남",
    "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
    "경상북도": "경북", "경상남도": "경남",
    "제주도": "제주", "제주특별자치도": "제주",
}


@dataclass(frozen=True)
class Region:
    sido: str
    sigungu: str
    capital: bool
    leap_grade: str          # 수도권 / 일반 / 우대 / 특별
    tax_tier: str            # I / II / III / IV
    tax_tier_confirmed: bool
    lawd_codes: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""

    def as_dict(self) -> dict:
        """조건 판정용 dict (eligibility의 'work_region.leap_grade' 같은 필드)."""
        return {
            "sido": self.sido, "sigungu": self.sigungu, "capital": self.capital,
            "leap_grade": self.leap_grade, "tax_tier": self.tax_tier,
            "tax_tier_confirmed": self.tax_tier_confirmed,
        }


def normalize_sido(sido: str) -> str:
    s = sido.strip()
    return SIDO_ALIASES.get(s, s)


class RegionTable:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self._rows: dict[tuple[str, str], Region] = {}
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                region = Region(
                    sido=r["sido"], sigungu=r["sigungu"],
                    capital=r["capital"] == "true",
                    leap_grade=r["leap_grade"], tax_tier=r["tax_tier"],
                    tax_tier_confirmed=r["tax_tier_confirmed"] == "true",
                    lawd_codes=tuple(c for c in r["lawd_codes"].split(";") if c),
                    note=r["note"],
                )
                self._rows[(region.sido, region.sigungu)] = region

    def names(self) -> list[tuple[str, str]]:
        return list(self._rows)

    def lookup(self, sido: str, sigungu: str = "*") -> Region:
        sido = normalize_sido(sido)
        sigungu = (sigungu or "*").strip()
        exact = self._rows.get((sido, sigungu))
        if exact:
            return exact
        default = self._rows.get((sido, "*"))
        if default is None:
            raise KeyError(f"알 수 없는 시도: {sido}")
        # 기본값을 쓰되 시군구 이름은 입력값으로 남긴다
        return Region(default.sido, sigungu, default.capital, default.leap_grade,
                      default.tax_tier, default.tax_tier_confirmed, (), default.note)
