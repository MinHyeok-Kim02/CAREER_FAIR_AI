"""전월세 실거래가 → 지역별 월세 기준값.

시연 중 API 장애·호출 한도에 흔들리지 않도록 '캐시 우선'으로 동작한다.
1) scripts/build_rent_cache.py 가 국토교통부 API를 호출해 data/rent_cache.csv 를 만든다 (사전 작업).
2) 앱은 rent_cache.csv 만 읽는다. 캐시에 없는 지역은 사용자가 월세를 직접 입력한다.

출처: 국토교통부 전월세 실거래가 자료 3종(단독/다가구, 연립다세대, 오피스텔), 공공데이터포털.
"""
from __future__ import annotations

import csv
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "rent_cache.csv"

ENDPOINTS = {
    "단독다가구": "https://apis.data.go.kr/1613000/RTMSDataSvcSHRent/getRTMSDataSvcSHRent",
    "연립다세대": "https://apis.data.go.kr/1613000/RTMSDataSvcRHRent/getRTMSDataSvcRHRent",
    "오피스텔": "https://apis.data.go.kr/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent",
}
# 응답 태그 이름이 버전에 따라 영문/한글로 다르다 → 둘 다 받는다
TAGS = {
    "deposit": ("deposit", "보증금액", "보증금"),
    "monthly_rent": ("monthlyRent", "월세금액", "월세"),
    "area": ("excluUseAr", "전용면적", "totalFloorAr", "계약면적"),
}
MAX_AREA_M2 = 40.0          # 1인 가구 기준: 전용 40㎡ 이하만 (면적 정보가 없는 행은 포함)
CACHE_FIELDS = ["sido", "sigungu", "lawd_codes", "months", "samples", "median_monthly_rent",
                "median_deposit", "max_area_m2", "fetched_at"]


@dataclass(frozen=True)
class RentRef:
    monthly_rent: int        # 원
    deposit: int             # 원
    samples: int
    months: str              # 예: 202608-202610
    fetched_at: str


# ---------------------------------------------------------------- 앱에서 쓰는 부분 (네트워크 없음)
def load_cache(path: Path | str = CACHE_PATH) -> dict[tuple[str, str], RentRef]:
    p = Path(path)
    if not p.exists():
        return {}
    out = {}
    with open(p, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[(r["sido"], r["sigungu"])] = RentRef(
                int(r["median_monthly_rent"]), int(r["median_deposit"]), int(r["samples"]),
                r["months"], r["fetched_at"])
    return out


def lookup(cache: dict, sido: str, sigungu: str) -> RentRef | None:
    """시군구 → 없으면 시도 전체('*') → 없으면 None(사용자 직접 입력)."""
    return cache.get((sido, sigungu)) or cache.get((sido, "*"))


# ---------------------------------------------------------------- 캐시 만들 때만 쓰는 부분
def _num(text: str | None) -> float | None:
    if text is None:
        return None
    t = text.strip().replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def _first(item: ET.Element, names: tuple[str, ...]) -> float | None:
    for n in names:
        el = item.find(n)
        if el is not None and el.text and el.text.strip():
            return _num(el.text)
    return None


def parse_items(xml_text: str) -> tuple[list[dict], int]:
    """응답 XML → [{deposit, monthly_rent, area}] (금액은 원 단위), 전체 건수."""
    root = ET.fromstring(xml_text)
    code = root.findtext(".//resultCode")
    if code not in (None, "00", "000"):
        raise RuntimeError(f"API 오류 {code}: {root.findtext('.//resultMsg')}")
    rows = []
    for item in root.iter("item"):
        dep, rent = _first(item, TAGS["deposit"]), _first(item, TAGS["monthly_rent"])
        if dep is None or rent is None:
            continue
        rows.append({"deposit": dep * 10_000, "monthly_rent": rent * 10_000,
                     "area": _first(item, TAGS["area"])})
    total = int(_num(root.findtext(".//totalCount")) or len(rows))
    return rows, total


def fetch(session, service_key: str, kind: str, lawd_cd: str, ym: str, page_size: int = 1000) -> list[dict]:
    rows, page = [], 1
    while True:
        r = session.get(ENDPOINTS[kind], params={
            "serviceKey": service_key, "LAWD_CD": lawd_cd, "DEAL_YMD": ym,
            "pageNo": page, "numOfRows": page_size}, timeout=20)
        r.raise_for_status()
        got, total = parse_items(r.text)
        rows += got
        if page * page_size >= total or not got:
            return rows
        page += 1


def summarize(rows: list[dict]) -> tuple[int, int, int] | None:
    """월세 계약(월세 > 0)이고 면적 조건에 맞는 행의 (월세 중앙값, 보증금 중앙값, 건수)."""
    pick = [r for r in rows if r["monthly_rent"] > 0 and (r["area"] is None or r["area"] <= MAX_AREA_M2)]
    if not pick:
        return None
    return (round(statistics.median(r["monthly_rent"] for r in pick)),
            round(statistics.median(r["deposit"] for r in pick)), len(pick))
