"""결과 설명 (18번).

1) 엔진 결과 → 설명용 사실(facts). 금액은 모두 여기서 만 원 단위 문자열로 만든다.
2) LLM이 facts만 보고 설명문을 쓴다. 새 숫자를 만들지 말라고 지시한다.
3) 코드가 설명문 속 숫자를 facts의 숫자와 대조한다. 하나라도 없는 숫자가 있으면
   LLM 문장을 버리고 템플릿 문장을 쓴다. → 화면의 금액은 항상 엔진 계산값과 같다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .llm import LLMUnavailable

LABELS = {
    "age": "나이", "employment_type": "고용 형태", "company_size": "회사 규모",
    "in_industrial_complex": "산업단지 입주", "company_participating": "도약장려금 참여 기업",
    "residence_type": "거주 형태", "homeless": "무주택", "income_median_ratio": "소득(기준 중위소득 대비 %)",
    "parent_household_median_ratio": "부모 가구 소득", "income_annual": "연봉", "deposit": "보증금",
    "monthly_rent": "월세", "relocating": "이사 여부", "housing_trade_amount": "보증금+월세×100",
    "work_region.leap_grade": "근무지 지역 구분", "work_region.capital": "수도권 여부",
    "residence_region.sido": "거주 시도",
}
OPS = {"eq": "이어야 함", "in": "중 하나여야 함", "between": "사이여야 함", "lt": "미만이어야 함",
       "lte": "이하여야 함", "gt": "초과여야 함", "gte": "이상이어야 함"}
PERIOD_NAMES = {12: "1년", 24: "2년", 60: "5년"}


def man(won: float) -> str:
    """원 → '1,440만 원' (만 원 단위 반올림)."""
    return f"{round(won / 10_000):,}만 원"


def humanize_reason(reason: str) -> str:
    """엔진 판정 문자열 → 사람이 읽는 이유."""
    m = re.match(r"([\w.]+)=(.+?) \(조건 (\w+) (.+)\)$", reason)
    if not m:
        m2 = re.match(r"([\w.]+): 입력 없음", reason)
        return f"{LABELS.get(m2.group(1), m2.group(1))} 정보 없음" if m2 else reason
    f, actual, op, expected = m.groups()
    yes_no = {"True": "예", "False": "아니오"}
    actual, expected = actual.strip("'"), expected.strip("'")
    actual, expected = yes_no.get(actual, actual), yes_no.get(expected, expected)
    if f == "income_median_ratio":
        actual, expected = f"{float(actual):.0f}%", f"{expected}%"
    expected = expected.replace("[", "").replace("]", "").replace("'", "")
    return f"{LABELS.get(f, f)}: {actual} (조건: {expected} {OPS.get(op, op)})"


# ---------------------------------------------------------------- 사실 정리
def build_facts(a, b, period: int = 24) -> dict:
    """OfferResult 두 개 → 설명에 쓸 사실. 숫자는 모두 문자열(만 원)로 넣는다."""
    def side(r):
        p = r.periods[period]
        return {
            "오퍼": r.name,
            "실질소득": man(p["real_income"]), "세후연봉": man(p["net_salary"]),
            "혜택합계": man(p["benefits"]), "주거비": man(p["housing_cost"]),
            "이사초기비용": man(p["one_time_cost"]),
            "선정시추가": man(p["selective"]), "저축시추가": man(p["savings"]),
            "확인필요금액": man(p["conditional"]),
            "적용제도": [{"이름": x["name"], "금액": man(x["amounts"][period]),
                         "구분": {"main": "합계 포함", "selective": "선정 시 추가", "savings": "저축 시 추가"}[x["bucket"]]
                         if x["state"] != "ask" else "확인 필요",
                         "예정제도": x["status"] == "planned"} for x in r.benefits if x["amounts"][period]],
        }
    pa, pb = a.periods[period], b.periods[period]
    diff = {k: pa[k] - pb[k] for k in ("real_income", "net_salary", "benefits", "housing_cost", "one_time_cost")}
    winner, loser = (a, b) if diff["real_income"] >= 0 else (b, a)
    drivers = sorted([("세후 연봉", diff["net_salary"]), ("제도 혜택", diff["benefits"]),
                      ("주거비", -diff["housing_cost"]), ("이사·초기 비용", -diff["one_time_cost"])],
                     key=lambda x: -abs(x[1]))
    return {
        "기간": PERIOD_NAMES.get(period, f"{period}개월"),
        "앞선오퍼": winner.name, "뒤진오퍼": loser.name, "차이": man(abs(diff["real_income"])),
        "차이요인": [{"항목": k, "A기준차이": man(v), "A에유리": v > 0} for k, v in drivers if round(v / 10_000)],
        "A": side(a), "B": side(b),
        "예정제도포함": a.include_planned or b.include_planned,
    }


# ---------------------------------------------------------------- 숫자 검증
def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def allowed_numbers(facts: dict) -> set[str]:
    s = numbers_in(json.dumps(facts, ensure_ascii=False))
    return s | {"1", "2", "5", "12", "24", "60"} | {str(y) for y in range(2026, 2031)}   # 기간·연도 표현


@dataclass
class Report:
    text: str
    source: str                         # llm / template
    rejected_numbers: list[str] = field(default_factory=list)


def template_text(f: dict) -> str:
    lines = [f"{f['기간']} 기준으로 '{f['앞선오퍼']}' 오퍼가 '{f['뒤진오퍼']}' 오퍼보다 실질소득이 {f['차이']} 많습니다."]
    if f["차이요인"]:
        top = f["차이요인"][0]
        who = f["A"]["오퍼"] if top["A에유리"] else f["B"]["오퍼"]
        lines.append(f"가장 큰 차이 요인은 {top['항목']}로, '{who}' 오퍼 쪽이 {top['A기준차이'].lstrip('-')} 유리합니다.")
    for side in (f["A"], f["B"]):
        if side["확인필요금액"] != "0만 원":
            lines.append(f"'{side['오퍼']}' 오퍼는 확인이 필요한 조건이 있어 {side['확인필요금액']}을 합계에서 뺐습니다.")
    if f["예정제도포함"]:
        lines.append("2027년 이후 입사라 정부안(예정) 제도를 적용했으며, 국회·시행령 확정 과정에서 달라질 수 있습니다.")
    return " ".join(lines)


SYSTEM = """너는 두 취업 오퍼의 실질소득 비교 결과를 사회초년생에게 설명하는 도우미다.
규칙:
- 아래 '사실'에 있는 숫자와 표현만 쓴다. 새로운 숫자, 비율, 금액을 계산하거나 만들지 않는다.
- 금액은 사실에 적힌 문자열 그대로 쓴다 (예: '1,440만 원').
- 4~6문장, 존댓말. 어느 쪽이 얼마나 유리한지, 가장 큰 차이 요인, 확인 필요·선정 시 추가·저축 시 추가 항목을 설명한다.
- 예정제도포함이 true면 정부안이라 바뀔 수 있다고 한 문장 덧붙인다.
- 어느 회사를 고르라고 단정하지 않는다. 연봉 외 요인(직무, 성장, 생활)도 함께 고려하라고 마무리한다."""


def explain(facts: dict, llm) -> Report:
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "사실(JSON):\n" + json.dumps(facts, ensure_ascii=False, indent=1)}]
    try:
        text = llm.chat(msgs, temperature=0.3, max_tokens=700).strip()
    except LLMUnavailable:
        return Report(template_text(facts), "template")
    bad = sorted(numbers_in(text) - allowed_numbers(facts))
    if bad or not text:
        return Report(template_text(facts), "template", bad)
    return Report(text, "llm")
