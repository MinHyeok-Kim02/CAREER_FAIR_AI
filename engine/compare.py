"""오퍼 1개의 기간별 실질소득 계산과 오퍼 비교.

실질소득(기간) = 세후 연봉 + 실질소득 합계에 넣는 혜택(main) − 주거비 − 이사·초기 비용
선정형 혜택(selective), 저축형 혜택(savings), 아직 답하지 않은 조건이 있는 혜택(conditional)은
합계에 넣지 않고 따로 낸다.
생활비(식비·교통비) 차이는 반영하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import eligibility as el
from . import tax as taxmod
from .region import RegionTable

PLANNED_AUTO_FROM = "2027-01-01"   # 이 날짜 이후 입사면 개편안(예정 제도)을 자동 적용


def planned_default(hire_date: str | None) -> bool:
    return bool(hire_date) and hire_date >= PLANNED_AUTO_FROM


@dataclass
class OfferResult:
    name: str
    include_planned: bool
    periods: dict[int, dict] = field(default_factory=dict)    # 개월 → 항목별 누적 금액
    benefits: list[dict] = field(default_factory=list)        # 적용된 제도
    not_applied: list[dict] = field(default_factory=list)     # 해당 없는 제도와 이유
    asks: list[str] = field(default_factory=list)             # 사용자에게 물어볼 필드


def _net_salary_by_month(ctx: dict, payroll: dict, assumptions: dict, horizon: int) -> dict[int, int]:
    start_year = int((ctx.get("hire_date") or "2027-01-01")[:4])
    nontax = assumptions["nontaxable_meal_monthly"]["value"]
    out = {}
    for y in range((horizon + 11) // 12):
        salary = el.salary_in_year(ctx, assumptions, y + 1)
        net = taxmod.net_income(payroll, salary, start_year + y, nontax).net
        for m in range(12 * y + 1, min(12 * (y + 1), horizon) + 1):
            out[m] = net / 12
    return out


def _housing_by_month(ctx: dict, assumptions: dict, horizon: int) -> dict[int, float]:
    h = assumptions["housing"].get(ctx.get("residence_type") or "", {})
    rent = ctx.get("monthly_rent") or 0
    fee = h.get("maintenance_fee", 0)
    deposit = ctx.get("deposit") or 0
    opp = deposit * assumptions["deposit_opportunity_rate"]["value"] / 12
    return {m: rent + fee + opp for m in range(1, horizon + 1)}


def _one_time_costs(ctx: dict, assumptions: dict) -> int:
    mv = assumptions["moving"]
    cost = 0
    if ctx["relocating"]:
        same_sido = ctx["residence_region"]["sido"] == ctx.get("_home_sido")
        cost += mv["same_region"]["value"] if same_sido else mv["long_distance"]["value"]
    if ctx.get("residence_type") == "자취" and ctx["relocating"]:
        cost += mv["initial_setup"]["value"]
    return cost


def _sum_upto(series: dict[int, float], months: int) -> int:
    return round(sum(v for m, v in series.items() if m <= months))


def evaluate_offer(name: str, profile: dict, offer: dict, *, regions: RegionTable, payroll: dict,
                   assumptions: dict, programs: list[dict], include_planned: bool | None = None,
                   periods: tuple[int, ...] = (12, 24, 60)) -> OfferResult:
    if include_planned is None:
        include_planned = planned_default(offer.get("hire_date"))
    horizon = max(periods)
    ctx = el.build_context(profile, offer, regions, payroll, assumptions)
    ctx["_home_sido"] = regions.lookup(profile["home_sido"], profile.get("home_sigungu", "*")).sido
    res = OfferResult(name, include_planned)

    lines = {"main": {}, "selective": {}, "savings": {}, "conditional": {}}
    for d in el.evaluate(programs, ctx, include_planned):
        tl = el.timeline(d, ctx, payroll, assumptions, horizon)
        b = "conditional" if d.needs_input else el.bucket(d)
        for m, v in tl.items():
            lines[b][m] = lines[b].get(m, 0) + v
        res.benefits.append({
            "id": d.program["id"], "name": d.program["name"], "bucket": b,
            "status": d.program["status"], "state": d.check.state, "flagged": d.flagged,
            "asks": d.check.asks, "warns": d.check.warns,
            "uncertain": d.program["benefit"].get("uncertain", []),
            "amounts": {p: el.cumulative(tl, p) for p in periods},
            "sources": d.program["sources"], "note": d.program.get("note", ""),
        })
        res.asks += [a for a in d.check.asks if a not in res.asks]
    for prog, why in el.rejected(programs, ctx, include_planned):
        res.not_applied.append({"id": prog["id"], "name": prog["name"], "reasons": why})

    net = _net_salary_by_month(ctx, payroll, assumptions, horizon)
    housing = _housing_by_month(ctx, assumptions, horizon)
    one_time = _one_time_costs(ctx, assumptions)
    for p in periods:
        row = {
            "net_salary": _sum_upto(net, p),
            "benefits": _sum_upto(lines["main"], p),
            "housing_cost": _sum_upto(housing, p),
            "one_time_cost": one_time,
            "selective": _sum_upto(lines["selective"], p),
            "savings": _sum_upto(lines["savings"], p),
            "conditional": _sum_upto(lines["conditional"], p),
        }
        row["real_income"] = row["net_salary"] + row["benefits"] - row["housing_cost"] - row["one_time_cost"]
        res.periods[p] = row
    return res
