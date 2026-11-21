"""페르소나 4명 x 오퍼 2개 검증. 계산 근거는 tests/EXPECTED.md.

실행: python tests/test_personas.py   (결과표 출력 + 검증)
      python -m pytest tests/         (pytest가 있으면)

기대값 종류
- 손계산: 제도 혜택, 주거비, 이사·초기 비용, 4,000만 원 세금 1건 → 규칙 JSON이 바뀌면 여기서 실패하는 게 정상
- 회귀값: 세후 연봉(엔진 출력 고정) → tax.py나 payroll_2026.json을 고치면 다시 확인 후 갱신
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.compare import evaluate_offer, planned_default          # noqa: E402
from engine.eligibility import load_assumptions, load_programs     # noqa: E402
from engine.region import RegionTable                              # noqa: E402
from engine.tax import load_payroll, net_income                    # noqa: E402

REGIONS, PAYROLL, ASSUMP, PROGRAMS = RegionTable(), load_payroll(), load_assumptions(), load_programs()
PERSONAS = json.load(open(ROOT / "samples" / "personas.json", encoding="utf-8"))["personas"]

# ---------------------------------------------------------------- 손계산 기대값
# (페르소나, 오퍼 순서) → {제도 id: (묶음, 24개월 누적, 60개월 누적)}  ※ 소득세 감면은 금액 대신 None(세금 계산 의존)
BENEFITS = {
    ("P1", 0): {"leap_youth_incentive_2027": ("main", 14_400_000, 14_400_000),
                "youth_future_savings_2027_general": ("savings", 0, 1_080_000)},
    ("P1", 1): {"leap_youth_incentive_2027": ("conditional", 4_800_000, 4_800_000),
                "seoul_youth_rent": ("selective", 2_400_000, 2_400_000),
                "seoul_moving_support": ("selective", 400_000, 400_000),
                "sme_income_tax_reform": ("main", None, None),
                "youth_future_savings_2027_sme": ("savings", 0, 2_700_000)},
    ("P2", 0): {"leap_youth_incentive_2027": ("conditional", 4_800_000, 4_800_000),
                "sme_income_tax_reform": ("main", None, None),
                "youth_future_savings_2027_sme": ("savings", 0, 2_700_000)},
    ("P2", 1): {"leap_youth_incentive_2027": ("main", 14_400_000, 14_400_000),
                "sme_income_tax_reform": ("main", None, None),
                "youth_future_savings_2027_local": ("savings", 0, 4_500_000)},
    ("P3", 0): {"leap_youth_incentive_2027": ("conditional", 4_800_000, 4_800_000),
                "youth_future_savings_2027_general": ("savings", 0, 1_080_000)},
    ("P3", 1): {"leap_youth_incentive_2027": ("main", 14_400_000, 14_400_000),
                "youth_future_savings_2027_general": ("savings", 0, 1_080_000)},
    ("P4", 0): {"leap_youth_incentive_2027": ("conditional", 14_400_000, 14_400_000),
                "sme_income_tax_reform": ("main", None, None),
                "youth_future_savings_2027_local": ("savings", 0, 4_500_000)},
    ("P4", 1): {"leap_youth_incentive_2027": ("conditional", 4_800_000, 4_800_000),
                "sme_income_tax_reform": ("main", None, None),
                "youth_future_savings_2027_sme": ("savings", 0, 2_700_000)},
}
# (월 주거비, 이사·초기 비용)  월 주거비 = 월세 + 관리비 10만 + 보증금 x 3% / 12
COSTS = {
    ("P1", 0): (0, 0),
    ("P1", 1): (520_000 + 100_000 + 25_000, 1_000_000 + 1_000_000),
    ("P2", 0): (0, 0),
    ("P2", 1): (300_000 + 100_000 + 5_000, 1_000_000 + 1_000_000),
    ("P3", 0): (0, 0),
    ("P3", 1): (50_000, 1_000_000),           # 기숙사: 오퍼에 적힌 기숙사비 5만, 이사비만(자취 아님)
    ("P4", 0): (0, 0),
    ("P4", 1): (520_000 + 100_000 + 25_000, 1_000_000 + 1_000_000),
}
# 세후 연봉 누적 (회귀값). P1 서울 중소는 연봉과 별도인 식대 월 20만(비과세)이 더해져 연 240만 많다.
NET_SALARY = {
    ("P1", 0): (31_385_155, 63_522_045, 164_648_129), ("P1", 1): (36_856_044, 74_547_349, 192_884_299),
    ("P2", 0): (32_920_599, 66_634_697, 172_754_374), ("P2", 1): (29_849_710, 60_409_392, 156_541_888),
    ("P3", 0): (35_991_488, 72_862_290, 188_993_680), ("P3", 1): (33_688_322, 68_191_023, 176_815_368),
    ("P4", 0): (28_287_556, 57_269_679, 148_408_582), ("P4", 1): (30_617_433, 61_965_719, 160_595_010),
}


def run_all():
    out = {}
    for per in PERSONAS:
        for i, offer in enumerate(per["offers"]):
            out[(per["id"], i)] = (per, offer, evaluate_offer(
                offer["name"], per["profile"], offer, regions=REGIONS, payroll=PAYROLL,
                assumptions=ASSUMP, programs=PROGRAMS))
    return out


RESULTS = run_all()


def test_tax_hand_calculation():
    """연봉 4,000만 원, 2027년(국민연금 5%) 손계산 — tests/EXPECTED.md 1절."""
    r = net_income(PAYROLL, 40_000_000, 2027)
    assert (r.pension, r.health, r.long_term_care, r.employment) == (2_000_000, 1_438_000, 188_953, 360_000)
    assert r.income_tax == 1_415_457
    reduced = net_income(PAYROLL, 40_000_000, 2027, reduction_rate=0.9, reduction_cap=2_000_000)
    assert abs(reduced.reduction - 1_525_030) <= 10


def test_planned_toggle_default():
    assert planned_default("2027-01-04") is True
    assert planned_default("2026-12-01") is False
    assert all(res.include_planned for _, _, res in RESULTS.values())


def test_benefits():
    for key, expected in BENEFITS.items():
        res = RESULTS[key][2]
        got = {b["id"]: b for b in res.benefits}
        assert set(got) == set(expected), (key, sorted(got), sorted(expected))
        for pid, (bucket, m24, m60) in expected.items():
            b = got[pid]
            bucket_got = "conditional" if b["state"] == "ask" else b["bucket"]
            assert bucket_got == bucket, (key, pid, bucket_got)
            if m24 is not None:
                assert (b["amounts"][24], b["amounts"][60]) == (m24, m60), (key, pid, b["amounts"])


def test_costs():
    for key, (monthly, one_time) in COSTS.items():
        res = RESULTS[key][2]
        assert res.periods[24]["housing_cost"] == round(monthly * 24), key
        assert res.periods[60]["housing_cost"] == round(monthly * 60), key
        assert res.periods[24]["one_time_cost"] == one_time, key


def test_net_salary_regression():
    for key, expected in NET_SALARY.items():
        res = RESULTS[key][2]
        assert tuple(res.periods[p]["net_salary"] for p in (12, 24, 60)) == expected, key


def test_asks():
    """참여기업 여부를 모르는 오퍼는 질문 대상이 된다."""
    assert RESULTS[("P1", 1)][2].asks == ["company_participating"]
    assert RESULTS[("P1", 0)][2].asks == []
    assert RESULTS[("P4", 0)][2].asks == ["company_participating"]


def test_real_income_formula():
    for _, _, res in RESULTS.values():
        for row in res.periods.values():
            assert row["real_income"] == (row["net_salary"] + row["benefits"]
                                          - row["housing_cost"] - row["one_time_cost"])


def print_table():
    man = lambda v: f"{v / 10_000:,.0f}"
    print("단위: 만 원 | 실질소득 = 세후 연봉 + 혜택 − 주거비 − 이사·초기 비용 | 입사 2027-01-04, 개편안 적용")
    print(f"{'페르소나 / 오퍼':<34}{'2년 실질':>10}{'5년 실질':>10}{'선정시+':>9}{'저축시+':>9}{'확인필요':>9}")
    for per in PERSONAS:
        print(per["name"])
        for i, offer in enumerate(per["offers"]):
            r = RESULTS[(per["id"], i)][2].periods
            print(f"  {offer['name']:<32}{man(r[24]['real_income']):>10}{man(r[60]['real_income']):>10}"
                  f"{man(r[60]['selective']):>9}{man(r[60]['savings']):>9}{man(r[60]['conditional']):>9}")


if __name__ == "__main__":
    print_table()
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n검증 {len(tests)}개 통과")
