"""data/rent_cache.csv 생성 (국토교통부 전월세 실거래가 API 3종).

사용법 (저장소 폴더에서):
    pip install requests
    python scripts/build_rent_cache.py              # 최근 3개월, regions.csv에 법정동코드가 있는 지역
    python scripts/build_rent_cache.py --months 202608 202609 202610

인증키(Decoding)는 다음 순서로 찾는다. 코드나 명령줄에 직접 적지 않는다.
    1) 환경변수 DATA_GO_KR_KEY
    2) .streamlit/secrets.toml 의 DATA_GO_KR_KEY (gitignore 대상)
    3) 화면에 입력 (입력 내용은 표시·기록되지 않음)
"""
from __future__ import annotations

import argparse
import csv
import getpass
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests                                   # noqa: E402
from engine.rent import CACHE_FIELDS, CACHE_PATH, ENDPOINTS, MAX_AREA_M2, fetch, summarize  # noqa: E402


def find_key() -> str:
    if os.environ.get("DATA_GO_KR_KEY"):
        return os.environ["DATA_GO_KR_KEY"]
    secrets = ROOT / ".streamlit" / "secrets.toml"
    if secrets.exists():
        for line in secrets.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATA_GO_KR_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return getpass.getpass("공공데이터 인증키(Decoding)를 붙여 넣으세요 (화면에 보이지 않음): ").strip()


def recent_months(n: int = 3, lag: int = 1) -> list[str]:
    """신고 지연을 고려해 이번 달과 직전 lag개월은 빼고, 그 전 n개월."""
    y, m = date.today().year, date.today().month
    out = []
    for i in range(lag + 1, lag + 1 + n):
        yy, mm = y, m - i
        while mm <= 0:
            yy, mm = yy - 1, mm + 12
        out.append(f"{yy}{mm:02d}")
    return sorted(out)


def target_regions() -> list[dict]:
    with open(ROOT / "data" / "regions.csv", encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["lawd_codes"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="*", help="YYYYMM 목록 (기본: 최근 3개월)")
    args = ap.parse_args()
    months = args.months or recent_months()
    key = find_key()
    session = requests.Session()
    regions = target_regions()
    calls = sum(len(r["lawd_codes"].split(";")) for r in regions) * len(ENDPOINTS) * len(months)
    print(f"대상 지역 {len(regions)}곳, 기간 {months[0]}~{months[-1]}, 예상 호출 {calls}회 (페이지 추가 시 더 늘어남)")

    out_rows, errors = [], 0
    for reg in regions:
        rows = []
        for code in reg["lawd_codes"].split(";"):
            for kind in ENDPOINTS:
                for ym in months:
                    try:
                        rows += fetch(session, key, kind, code, ym)
                    except Exception as e:      # 한 건 실패해도 나머지는 계속
                        errors += 1
                        print(f"  실패 {reg['sido']} {code} {kind} {ym}: {type(e).__name__} {str(e)[:80]}")
        s = summarize(rows)
        label = f"{reg['sido']} {reg['sigungu']}"
        if s is None:
            print(f"{label}: 월세 표본 없음 → 캐시에 넣지 않음")
            continue
        rent, deposit, n = s
        print(f"{label}: 월세 중앙값 {rent:,}원, 보증금 중앙값 {deposit:,}원 (표본 {n}건 / 전체 {len(rows)}건)")
        out_rows.append({"sido": reg["sido"], "sigungu": reg["sigungu"], "lawd_codes": reg["lawd_codes"],
                         "months": f"{months[0]}-{months[-1]}", "samples": n,
                         "median_monthly_rent": rent, "median_deposit": deposit,
                         "max_area_m2": MAX_AREA_M2, "fetched_at": date.today().isoformat()})

    if not out_rows:
        print("저장할 결과가 없습니다. 인증키·승인 상태를 확인하세요.")
        sys.exit(1)
    with open(CACHE_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(out_rows)
    print(f"\n저장: {CACHE_PATH.relative_to(ROOT)} ({len(out_rows)}개 지역, 실패 호출 {errors}회)")


if __name__ == "__main__":
    main()
