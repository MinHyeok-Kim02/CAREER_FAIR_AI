"""제도 판정과 혜택 계산 (rules/programs/*.json, 형식은 rules/SCHEMA.md).

흐름: build_context(입력 → 판정용 필드) → evaluate(제도마다 조건 판정)
      → select(그룹당 1개 선택) → timeline(월별 지급액).
이 모듈은 제도 이름을 모른다. 새 제도는 JSON만 추가하면 된다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import tax as taxmod
from .region import RegionTable

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS_DIR = ROOT / "rules" / "programs"
ASSUMPTIONS_PATH = ROOT / "rules" / "assumptions.json"

# 조건 판정 상태
PASS, FAIL, ASK, WARN = "pass", "fail", "ask", "warn"
MISSING = object()


# ---------------------------------------------------------------- 불러오기
def load_programs(directory: Path | str = PROGRAMS_DIR) -> list[dict]:
    progs = []
    for path in sorted(Path(directory).glob("*.json")):
        with open(path, encoding="utf-8") as f:
            progs.append(json.load(f))
    return progs


def load_assumptions(path: Path | str = ASSUMPTIONS_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 입력 → 판정용 필드
def build_context(profile: dict, offer: dict, regions: RegionTable, payroll: dict,
                  assumptions: dict) -> dict:
    """사용자 정보(profile)와 오퍼 1개(offer)를 조건 판정용 dict로 합친다.

    profile: age, home_sido, home_sigungu, homeless, parent_household_median_ratio, saving_monthly
    offer:   salary, sido, sigungu, company_size, in_industrial_complex, company_participating,
             employment_type, residence_type, monthly_rent, deposit, hire_date(YYYY-MM-DD)
    값을 모르면 None 또는 생략 → 조건의 on_unknown 규칙을 따른다.
    """
    work = regions.lookup(offer["sido"], offer.get("sigungu", "*"))
    home = regions.lookup(profile["home_sido"], profile.get("home_sigungu", "*"))
    residence_type = offer.get("residence_type")
    residence = home if residence_type == "본가" else work

    housing = assumptions["housing"].get(residence_type or "", {})
    monthly_rent = offer.get("monthly_rent", housing.get("monthly_rent"))
    deposit = offer.get("deposit", housing.get("deposit"))

    salary = offer["salary"]
    household = assumptions["household_size"]["value"]
    median = payroll["median_income_monthly"][str(household)]

    ctx = {
        "age": profile.get("age"),
        "homeless": profile.get("homeless"),
        "parent_household_median_ratio": profile.get("parent_household_median_ratio"),
        "saving_monthly": profile.get("saving_monthly", assumptions["saving_monthly"]["value"]),
        "salary": salary,
        "income_annual": salary,
        "income_median_ratio": salary / 12 / median * 100,
        "employment_type": offer.get("employment_type"),
        "company_size": offer.get("company_size"),
        "in_industrial_complex": offer.get("in_industrial_complex"),
        "company_participating": offer.get("company_participating"),
        "residence_type": residence_type,
        "monthly_rent": monthly_rent,
        "deposit": deposit,
        "hire_date": offer.get("hire_date"),
        "work_region": work.as_dict(),
        "residence_region": residence.as_dict(),
        "relocating": (residence.sido, residence.sigungu) != (home.sido, home.sigungu),
    }
    if deposit is not None and monthly_rent is not None:
        ctx["housing_trade_amount"] = deposit + monthly_rent * 100
    return ctx


def get_field(ctx: dict, dotted: str):
    cur = ctx
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur or cur[part] is None:
            return MISSING
        cur = cur[part]
    return cur


# ---------------------------------------------------------------- 조건 판정
@dataclass
class Check:
    state: str                                   # pass / fail / ask / warn
    asks: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)
    fails: list[str] = field(default_factory=list)


def _compare(op: str, actual, expected) -> bool:
    if op == "eq":
        return actual == expected
    if op == "in":
        return actual in expected
    if op == "between":
        return expected[0] <= actual <= expected[1]
    if op == "lt":
        return actual < expected
    if op == "lte":
        return actual <= expected
    if op == "gt":
        return actual > expected
    if op == "gte":
        return actual >= expected
    raise ValueError(f"알 수 없는 연산자: {op}")


def _check_one(cond: dict, ctx: dict) -> Check:
    if "any" in cond:
        return _check_any(cond["any"], ctx)
    name = cond["field"]
    actual = get_field(ctx, name)
    if actual is MISSING:
        mode = cond.get("on_unknown")
        if mode == "ask":
            return Check(ASK, asks=[name])
        if mode == "warn":
            return Check(WARN, warns=[name])
        return Check(FAIL, fails=[f"{name}: 입력 없음"])
    if _compare(cond["op"], actual, cond["value"]):
        return Check(PASS)
    return Check(FAIL, fails=[f"{name}={actual!r} (조건 {cond['op']} {cond['value']!r})"])


def _combine_all(checks: list[Check]) -> Check:
    """AND: 하나라도 fail이면 fail, 아니면 ask > warn > pass 순."""
    out = Check(PASS)
    for c in checks:
        out.asks += c.asks
        out.warns += c.warns
        out.fails += c.fails
    if out.fails:
        out.state = FAIL
    elif out.asks:
        out.state = ASK
    elif out.warns:
        out.state = WARN
    return out


def _check_any(branches: list[list[dict]], ctx: dict) -> Check:
    """OR of AND: 통과하는 묶음이 하나라도 있으면 그 결과, 없으면 확인이 필요한 묶음, 없으면 fail."""
    results = [check_all(branch, ctx) for branch in branches]
    for state in (PASS, WARN, ASK):
        for r in results:
            if r.state == state:
                return r
    return Check(FAIL, fails=[f for r in results for f in r.fails])


def check_all(conditions: list[dict], ctx: dict) -> Check:
    return _combine_all([_check_one(c, ctx) for c in conditions])


def _in_valid_period(program: dict, ctx: dict) -> bool:
    hire = ctx.get("hire_date")
    if not hire:
        return True
    valid = program.get("valid", {})
    return valid.get("hire_from", "0000") <= hire <= valid.get("hire_to", "9999")


# ---------------------------------------------------------------- 그룹별 선택
@dataclass
class Decision:
    program: dict
    check: Check

    @property
    def needs_input(self) -> bool:
        return self.check.state == ASK

    @property
    def flagged(self) -> bool:
        """결과에 '확인 필요'를 붙여야 하는지."""
        return bool(self.check.warns or self.check.asks or self.program["benefit"].get("uncertain"))


def evaluate(programs: list[dict], ctx: dict, include_planned: bool = False) -> list[Decision]:
    """그룹마다 적용할 제도 1개를 고른다. 모두 fail인 그룹은 빠진다."""
    by_group: dict[str, list[Decision]] = {}
    for prog in programs:
        if prog["status"] == "planned" and not include_planned:
            continue
        if not _in_valid_period(prog, ctx):
            continue
        check = check_all(prog["conditions"], ctx)
        if check.state == FAIL:
            continue
        by_group.setdefault(prog["group"], []).append(Decision(prog, check))
    return [max(cands, key=lambda d: d.program["priority"]) for cands in by_group.values()]


def rejected(programs: list[dict], ctx: dict, include_planned: bool = False) -> list[tuple[dict, list[str]]]:
    """해당하지 않는 제도와 이유 (설명용)."""
    out = []
    for prog in programs:
        if prog["status"] == "planned" and not include_planned:
            continue
        if not _in_valid_period(prog, ctx):
            out.append((prog, ["입사일이 적용 기간 밖"]))
            continue
        check = check_all(prog["conditions"], ctx)
        if check.state == FAIL:
            out.append((prog, check.fails))
    return out


# ---------------------------------------------------------------- 혜택 계산
def salary_in_year(ctx: dict, assumptions: dict, year_index: int) -> int:
    """입사 후 n년차(1부터) 연봉. 연봉 상승률 가정 적용."""
    g = assumptions["salary_growth_rate"]["value"]
    return round(ctx["salary"] * (1 + g) ** (year_index - 1))


def timeline(decision: Decision, ctx: dict, payroll: dict, assumptions: dict,
             horizon_months: int = 60) -> dict[int, int]:
    """{입사 후 개월: 지급액}. 근속 개월 수 기준이며 horizon_months를 넘는 지급은 제외."""
    b = decision.program["benefit"]
    kind = b["type"]
    out: dict[int, int] = {}

    def put(month: int, amount: float):
        if 1 <= month <= horizon_months and amount:
            out[month] = out.get(month, 0) + round(amount)

    if kind == "schedule":
        amount = b["table"][str(get_field(ctx, b["amount_by"]))] if "amount_by" in b else b["amount"]
        for m in b["at_months"]:
            put(m, amount)

    elif kind == "monthly":
        base = get_field(ctx, b["base_field"])
        per_month = min(base, b["cap"]) if base is not MISSING else 0
        for m in range(1, b["months"] + 1):
            put(m, per_month)

    elif kind == "tax_reduction":
        if "years_by" in b:
            years = b["table"][str(get_field(ctx, b["years_by"]))]
        else:
            years = b["years"]
        start_year = int((ctx.get("hire_date") or "2027-01-01")[:4])
        nontax = assumptions["nontaxable_meal_monthly"]["value"]
        for y in range(1, years + 1):
            saving = taxmod.tax_reduction_saving(
                payroll, salary_in_year(ctx, assumptions, y), start_year + y - 1,
                b["rate"], b["annual_cap"], nontax)
            for m in range(12 * (y - 1) + 1, 12 * y + 1):   # 원천징수 단계에서 매달 덜 떼는 것으로 분배
                put(m, saving / 12)

    elif kind == "match":
        base = get_field(ctx, b["base_field"])
        monthly = min(base, b["monthly_cap"]) if base is not MISSING else 0
        put(b["months"], monthly * b["rate"] * b["months"])   # 기여금은 만기에 한꺼번에 받는 것으로 표시

    elif kind == "one_time":
        put(b.get("at_month", 1), b["amount"])

    else:
        raise ValueError(f"알 수 없는 계산 방식: {kind}")
    return out


def cumulative(tl: dict[int, int], months: int) -> int:
    return sum(v for m, v in tl.items() if m <= months)


def bucket(decision: Decision) -> str:
    """결과 화면 묶음: main(실질소득 합계) / selective(선정 시 추가) / savings(저축 시 추가)."""
    p = decision.program
    if p["category"] == "savings":
        return "savings"
    if p["selection"] == "selective":
        return "selective"
    return "main"
