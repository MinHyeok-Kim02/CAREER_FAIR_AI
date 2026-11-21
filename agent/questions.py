"""부족한 정보 질문 (17번).

질문 문구와 선택지는 고정(LLM 미사용): 답이 계산에 그대로 쓰이므로 매번 같은 형태여야 한다.
각 질문에는 '이 답에 따라 얼마가 달라지는지'를 엔진으로 계산해 붙인다.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.compare import evaluate_offer

UNKNOWN = "모름"


@dataclass(frozen=True)
class Question:
    field: str
    label: str
    kind: str                       # choice / bool / number / date
    options: tuple = ()
    help: str = ""


QUESTIONS = {
    # 오퍼마다 필요한 값
    "salary": Question("salary", "연봉(세전, 원)", "number", help="기본급과 고정수당을 합친 금액. 변동 성과급은 빼고 입력"),
    "sido": Question("sido", "근무지 시도", "choice"),
    "sigungu": Question("sigungu", "근무지 시군구", "choice"),
    "company_size": Question("company_size", "회사 규모", "choice", ("중소", "중견", "대"),
                             "근속지원금·소득세 감면·청년미래적금 대상이 회사 규모로 갈립니다"),
    "employment_type": Question("employment_type", "고용 형태", "choice", ("정규직", "계약직")),
    "hire_date": Question("hire_date", "입사 예정일", "date"),
    "residence_type": Question("residence_type", "입사 후 어디에 사나요?", "choice", ("본가", "자취", "기숙사"),
                               "주거비와 월세 지원 대상 여부가 달라집니다"),
    "monthly_rent": Question("monthly_rent", "월세(원)", "number", help="기본값은 해당 지역 실거래가 중앙값"),
    "deposit": Question("deposit", "보증금(원)", "number", help="기본값은 해당 지역 실거래가 중앙값"),
    "company_participating": Question("company_participating", "회사가 청년일자리도약장려금 참여 기업인가요?", "bool",
                                      help="인사팀에 '도약장려금 참여(신청) 여부'를 물어보면 됩니다"),
    "in_industrial_complex": Question("in_industrial_complex", "근무지가 산업단지 안인가요?", "bool",
                                      help="중견기업은 산업단지 입주 기업만 근속지원금 대상입니다"),
    # 사람마다 필요한 값
    "age": Question("age", "만 나이", "number"),
    "homeless": Question("homeless", "본인 명의 주택이 없나요(무주택)?", "bool"),
}
REQUIRED_OFFER = ("salary", "sido", "company_size", "employment_type", "hire_date", "residence_type")
REQUIRED_PROFILE = ("age", "home_sido")


def missing_required(offer: dict) -> list[str]:
    """계산을 시작하려면 반드시 있어야 하는 오퍼 값 중 빠진 것."""
    need = list(REQUIRED_OFFER)
    if offer.get("residence_type") == "자취":
        need += ["monthly_rent", "deposit"]
    return [f for f in need if offer.get(f) is None]


def impact(field: str, profile: dict, offer: dict, ctx_kwargs: dict, months: int = 24) -> int:
    """예/아니오 질문의 답에 따라 실질소득(합계 + 확인 필요 금액)이 얼마나 달라지는지 (원)."""
    def total(value):
        r = evaluate_offer("x", profile, {**offer, field: value}, **ctx_kwargs).periods[months]
        return r["real_income"] + r["conditional"]
    return abs(total(True) - total(False))


def follow_up(result, profile: dict, offer: dict, ctx_kwargs: dict) -> list[dict]:
    """엔진이 '확인 필요(ask)'로 남긴 조건 → 질문 목록 (영향 금액 큰 순)."""
    out = []
    for f in result.asks:
        q = QUESTIONS.get(f)
        if q is None:
            continue
        amount = impact(f, profile, offer, ctx_kwargs) if q.kind == "bool" else 0
        out.append({"field": f, "question": q, "impact_24m": amount})
    return sorted(out, key=lambda x: -x["impact_24m"])
