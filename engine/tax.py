"""세후 연봉 계산 (rules/payroll_2026.json).

연말정산 기준 근사치: 1인 가구, 본인 기본공제 + 보험료 공제 + 근로소득세액공제 + 표준세액공제만 반영.
월별 간이세액표 원천징수와 차이가 있을 수 있다. 제도 JSON과는 독립적이고,
중소기업 취업자 소득세 감면만 reduction 인자로 받는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "rules" / "payroll_2026.json"


def load_payroll(path: Path | str = DEFAULT_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class TaxResult:
    salary: int                 # 연봉(세전)
    taxable_salary: int         # 총급여(비과세 제외)
    pension: int                # 국민연금
    health: int                 # 건강보험
    long_term_care: int         # 장기요양
    employment: int             # 고용보험
    income_tax: int             # 소득세 (결정세액)
    local_tax: int              # 지방소득세
    reduction: int              # 소득세 감면액 (감면 전 대비 줄어든 소득세+지방세)
    net: int                    # 세후 연 실수령액

    def as_dict(self) -> dict:
        return asdict(self)


def _pension_rate(p: dict, year: int) -> float:
    table = p["social_insurance_employee"]["national_pension"]["rate_by_year"]
    years = sorted(int(y) for y in table)
    use = max([y for y in years if y <= year], default=years[0])
    return table[str(use)]


def social_insurance(p: dict, salary: int, year: int, nontaxable_monthly: int = 0) -> dict:
    """근로자 부담 4대보험 (연간)."""
    si = p["social_insurance_employee"]
    monthly = salary / 12 - nontaxable_monthly
    np_cfg = si["national_pension"]
    pension_base = min(max(monthly, np_cfg["monthly_base_floor"]), np_cfg["monthly_base_cap"])
    pension = pension_base * _pension_rate(p, year) * 12
    health = monthly * si["health_insurance"]["rate"] * 12
    ltc = health * si["long_term_care"]["rate_of_health"]
    employment = monthly * si["employment_insurance"]["rate"] * 12
    return {"pension": round(pension), "health": round(health),
            "long_term_care": round(ltc), "employment": round(employment)}


def _earned_income_deduction(t: dict, taxable_salary: float) -> float:
    for row in t["earned_income_deduction"]:
        if row["upto"] is None or taxable_salary <= row["upto"]:
            d = row["base"] + (taxable_salary - row["over"]) * row["rate"]
            return min(d, t["earned_income_deduction_cap"])
    raise ValueError("근로소득공제 표 오류")


def _calculated_tax(t: dict, tax_base: float) -> float:
    for row in t["brackets"]:
        if row["upto"] is None or tax_base <= row["upto"]:
            return max(0.0, tax_base * row["rate"] - row["progressive_deduction"])
    raise ValueError("세율표 오류")


def _earned_income_credit(t: dict, calc_tax: float, taxable_salary: float) -> float:
    c = t["earned_income_tax_credit"]
    if calc_tax <= c["threshold"]:
        credit = calc_tax * c["rate_low"]
    else:
        credit = c["base_high"] + (calc_tax - c["threshold"]) * c["rate_high"]
    s = taxable_salary
    if s <= 33_000_000:
        limit = 740_000
    elif s <= 70_000_000:
        limit = max(660_000, 740_000 - (s - 33_000_000) * 0.008)
    elif s <= 120_000_000:
        limit = max(500_000, 660_000 - (s - 70_000_000) * 0.5)
    else:
        limit = max(200_000, 500_000 - (s - 120_000_000) * 0.5)
    return min(credit, limit)


def _income_tax(p: dict, taxable_salary: float, insurance: dict,
                reduction_rate: float = 0.0, reduction_cap: float = 0.0) -> tuple[float, float]:
    """(결정세액, 감면세액)을 돌려준다.

    감면이 있으면 근로소득세액공제를 감면 비율만큼 줄인다(산출세액 중 감면된 부분에는 공제 미적용).
    """
    t = p["income_tax"]
    earned_income = taxable_salary - _earned_income_deduction(t, taxable_salary)
    # 연금보험료 공제 + 특별소득공제(건강·장기요양·고용보험료)
    deductions = (t["basic_deduction_per_person"] + insurance["pension"] + insurance["health"]
                  + insurance["long_term_care"] + insurance["employment"])
    tax_base = max(0.0, earned_income - deductions)
    calc = _calculated_tax(t, tax_base)
    reduction = min(calc * reduction_rate, reduction_cap) if reduction_rate else 0.0
    ratio = reduction / calc if calc else 0.0
    credit = _earned_income_credit(t, calc, taxable_salary) * (1 - ratio)
    final = max(0.0, calc - reduction - credit - t["standard_tax_credit"])
    return final, reduction


def net_income(p: dict, salary: int, year: int, nontaxable_monthly: int = 0,
               reduction_rate: float = 0.0, reduction_cap: float = 0.0) -> TaxResult:
    """한 해의 세후 실수령액. reduction_rate>0이면 중소기업 취업자 소득세 감면을 적용한다."""
    taxable = salary - nontaxable_monthly * 12
    ins = social_insurance(p, salary, year, nontaxable_monthly)
    local_rate = p["income_tax"]["local_income_tax_rate"]
    base_tax, _ = _income_tax(p, taxable, ins)
    tax, _ = _income_tax(p, taxable, ins, reduction_rate, reduction_cap)
    tax, base_tax = round(tax), round(base_tax)
    local = round(tax * local_rate)
    saved = (base_tax - tax) + (round(base_tax * local_rate) - local)
    net = salary - sum(ins.values()) - tax - local
    return TaxResult(salary, round(taxable), ins["pension"], ins["health"], ins["long_term_care"],
                     ins["employment"], tax, local, saved, round(net))


def tax_reduction_saving(p: dict, salary: int, year: int, rate: float, cap: float,
                         nontaxable_monthly: int = 0) -> int:
    """감면으로 줄어드는 세금(소득세+지방소득세)만 돌려준다. eligibility의 tax_reduction이 사용."""
    return net_income(p, salary, year, nontaxable_monthly, rate, cap).reduction
