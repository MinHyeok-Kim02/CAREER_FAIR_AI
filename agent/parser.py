"""오퍼 원문(텍스트) → 엔진 입력.

LLM은 원문에서 값을 '찾아 옮기기'만 한다. 모르면 null, 추측 금지, 근거 문장 인용.
코드는 LLM 결과를 검증한다: 금액이 원문 숫자와 맞는지, 지역·선택지가 유효한지.
LLM을 쓸 수 없으면 정규식으로 연봉·시도·고용형태만 뽑고 나머지는 질문으로 넘긴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine.region import RegionTable, SIDO_ALIASES, normalize_sido

from .llm import LLMUnavailable

FIELDS = {
    "company_name": "회사 이름 (원문 그대로)",
    "salary": "연봉 총액(원, 정수). 기본급·고정수당 포함, 변동 성과급 제외. '3,600만원'·'3600'(만원 단위 표기) 모두 원으로 환산",
    "bonus_note": "성과급·인센티브 설명 (원문 요약, 금액 확정이 아니면 문장으로)",
    "sido": "근무지 시도 (예: 서울, 경기, 경북)",
    "sigungu": "근무지 시군구. 특별·광역시는 구(예: 마포구), 도는 시·군(예: 성남시, 구미시 — '성남시 분당구'는 성남시). 모르면 null",
    "in_industrial_complex": "근무지가 산업단지(국가·일반산단, '공단') 안이면 true, 밖이라고 적혀 있으면 false, 언급 없으면 null",
    "company_size": "'대' / '중견' / '중소' 중 하나. 원문에 규모가 명시된 경우만. 직원 수만 있으면 null",
    "employee_count": "직원 수 (정수, 원문에 있으면)",
    "employment_type": "'정규직' 또는 '계약직'. 수습은 정규직으로 본다",
    "hire_date": "입사 예정일 YYYY-MM-DD. '내년 1월 4일'처럼 연도가 상대적이면 기준일을 이용",
    "housing": "'기숙사' / '사택' / '주거지원금' / '없음' / null (언급 없음)",
    "housing_cost_monthly": "기숙사·사택 본인 부담액(원/월)",
    "housing_allowance_monthly": "주거지원금(원/월)",
    "meal_allowance_monthly": "식대(원/월)",
    "meal_included": "식대가 연봉에 포함이면 true, '별도 지급'이면 false, 불명확하면 null",
    "company_participating": "청년일자리도약장려금 참여(신청 완료)라고 명시되면 true, 불참 명시면 false, '확인 중'·언급 없음은 null",
}
ENUMS = {
    "company_size": {"대", "중견", "중소"},
    "employment_type": {"정규직", "계약직"},
    "housing": {"기숙사", "사택", "주거지원금", "없음"},
}
# 예/아니오 항목은 원문에 이 단어 중 하나라도 있어야 값으로 인정한다 (추측 차단)
GROUNDING_WORDS = {
    "in_industrial_complex": ("산단", "산업단지", "공단"),
    "company_participating": ("도약", "장려금"),
    "meal_included": ("식대", "식비"),
}
SYSTEM = """너는 채용 오퍼(처우 안내) 문서에서 정보를 옮겨 적는 도우미다.
규칙:
- 원문에 있는 내용만 옮긴다. 없거나 애매하면 null. 추측하지 않는다.
- 금액은 원 단위 정수로 바꾼다. '3,600만원'=36000000, 연봉 '3400'처럼 단위가 없고 문맥상 만원이면 34000000.
- evidence에는 각 값을 뽑은 원문 구절을 짧게 그대로 인용한다.
- 반드시 JSON 객체 하나로만 답한다."""


@dataclass
class ParsedOffer:
    values: dict
    evidence: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source: str = "llm"                 # llm / rule (LLM 장애 시)

    def missing(self, needed: tuple[str, ...]) -> list[str]:
        return [k for k in needed if self.values.get(k) is None]


def build_messages(text: str, today: str) -> list[dict]:
    spec = "\n".join(f'- "{k}": {v}' for k, v in FIELDS.items())
    user = (f"기준일(오늘): {today}\n\n추출할 항목:\n{spec}\n\n"
            '답 형식: {"values": {항목: 값 또는 null}, "evidence": {항목: "원문 인용"}}\n\n'
            f"오퍼 원문:\n<<<\n{text}\n>>>")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- 원문 숫자 대조
_KOR_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}


def _korean_amount(s: str) -> int | None:
    """'삼천육백만원' 같은 한글 금액 → 원. 만 단위 이하 표기만 다룬다."""
    m = re.search(r"([일이삼사오육칠팔구천백십]+)만\s*원", s)
    if not m:
        return None
    total, num = 0, 0
    for ch in m.group(1):
        if ch in _KOR_DIGITS:
            num = _KOR_DIGITS[ch]
        else:
            unit = {"천": 1000, "백": 100, "십": 10}[ch]
            total += (num or 1) * unit
            num = 0
    return (total + num) * 10_000


def amounts_in_text(text: str) -> set[int]:
    """원문에 나온 금액 후보(원). '36,000,000원', '3,600만원', '연봉 3400'."""
    out = set()
    for m in re.finditer(r"(\d[\d,]*)\s*(만\s*원|만|원)?(?![년.\-/\d])", text):
        n = int(m.group(1).replace(",", "") or 0)
        unit = (m.group(2) or "").replace(" ", "")
        if unit in ("만원", "만"):
            out.add(n * 10_000)
        elif unit == "원":
            out.add(n)
        elif 1000 <= n <= 20000:          # '연봉 3400' 처럼 만원 단위를 생략한 표기
            out.add(n * 10_000)
    k = _korean_amount(text)
    if k:
        out.add(k)
    return out


# ---------------------------------------------------------------- 검증
def validate(values: dict, text: str, regions: RegionTable) -> list[str]:
    """값을 고치거나 지우고, 사람이 확인할 경고를 돌려준다."""
    warns = []
    for k in FIELDS:
        values.setdefault(k, None)
    for k, allowed in ENUMS.items():
        if values[k] is not None and values[k] not in allowed:
            warns.append(f"{k} 값 '{values[k]}'은(는) 허용 값이 아니어서 비웠습니다.")
            values[k] = None
    for k in ("salary", "employee_count", "housing_cost_monthly", "housing_allowance_monthly",
              "meal_allowance_monthly"):
        v = values[k]
        if v is not None:
            try:
                values[k] = int(float(str(v).replace(",", "")))
            except ValueError:
                warns.append(f"{k} 값 '{v}'을(를) 숫자로 읽지 못해 비웠습니다.")
                values[k] = None
    s = values["salary"]
    if s is not None:
        if not 10_000_000 <= s <= 200_000_000:
            warns.append(f"연봉 {s:,}원은 범위를 벗어나 비웠습니다. 직접 입력해 주세요.")
            values["salary"] = None
        elif s not in amounts_in_text(text):
            warns.append(f"연봉 {s:,}원이 원문 숫자와 정확히 일치하지 않습니다. 확인해 주세요.")
    if values["sido"]:
        sido = normalize_sido(values["sido"])
        try:
            regions.lookup(sido, values["sigungu"] or "*")
            values["sido"] = sido
        except KeyError:
            warns.append(f"근무지 '{values['sido']}'을(를) 알 수 없어 비웠습니다.")
            values["sido"] = None
    if values["hire_date"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(values["hire_date"])):
        warns.append(f"입사일 '{values['hire_date']}' 형식이 달라 비웠습니다.")
        values["hire_date"] = None
    for k in ("in_industrial_complex", "meal_included", "company_participating"):
        if values[k] not in (True, False, None):
            values[k] = None
    # 원문에 관련 단어가 전혀 없는데 예/아니오를 답했다면 AI의 추측이다 → 비우고 사용자에게 묻는다
    for k, words in GROUNDING_WORDS.items():
        if values[k] is not None and not any(w in text for w in words):
            warns.append(f"'{k}' 값은 원문 근거가 없어 비웠습니다. 직접 확인해 주세요.")
            values[k] = None
    return warns


# ---------------------------------------------------------------- LLM 장애 시 규칙 추출
SIDO_WORDS = sorted(set(SIDO_ALIASES) | set(SIDO_ALIASES.values()), key=len, reverse=True)


def rule_extract(text: str, regions: RegionTable | None = None) -> dict:
    values = {k: None for k in FIELDS}
    m = re.search(r"연봉[^\d\n]{0,20}(\d[\d,]*)\s*(만\s*원|만|원)?", text)
    if m:
        n = int(m.group(1).replace(",", ""))
        unit = (m.group(2) or "").replace(" ", "")
        values["salary"] = n if unit == "원" else n * 10_000 if (unit or n <= 20000) else n
    for w in SIDO_WORDS:
        if w in text:
            values["sido"] = normalize_sido(w)
            break
    if regions is not None:                      # '구미'처럼 시군구 이름만 있는 경우
        for sido, sgg in regions.names():
            stem = sgg[:-1] if len(sgg) > 2 else sgg
            if sgg != "*" and stem in text:
                values["sido"], values["sigungu"] = sido, sgg
                break
    if "정규직" in text:
        values["employment_type"] = "정규직"
    elif "계약직" in text:
        values["employment_type"] = "계약직"
    return values


def parse_offer(text: str, llm, regions: RegionTable, today: str) -> ParsedOffer:
    try:
        data = llm.chat_json(build_messages(text, today))
        values = dict(data.get("values") or {})
        evidence = dict(data.get("evidence") or {})
        source = "llm"
    except (LLMUnavailable, ValueError) as e:
        values, evidence, source = rule_extract(text, regions), {}, "rule"
        pre = [f"AI 추출을 쓸 수 없어 기본 규칙으로 일부만 읽었습니다 ({type(e).__name__})."]
    else:
        pre = []
    warns = pre + validate(values, text, regions)
    return ParsedOffer(values, evidence, warns, source)


def to_engine_offer(parsed: dict, answers: dict | None = None) -> dict:
    """파싱 값 + 사용자 답변 → engine.compare 입력."""
    v = {**parsed, **(answers or {})}
    housing = v.get("housing")
    residence = v.get("residence_type") or {"기숙사": "기숙사", "사택": "기숙사"}.get(housing)
    offer = {
        "name": v.get("company_name") or "오퍼",
        "salary": v.get("salary"), "sido": v.get("sido"), "sigungu": v.get("sigungu") or "*",
        "company_size": v.get("company_size"), "in_industrial_complex": v.get("in_industrial_complex"),
        "company_participating": v.get("company_participating"),
        "employment_type": v.get("employment_type"), "hire_date": v.get("hire_date"),
        "residence_type": residence,
        "meal_allowance_monthly": v.get("meal_allowance_monthly"), "meal_included": v.get("meal_included"),
    }
    if residence == "기숙사" and v.get("housing_cost_monthly") is not None:
        offer["maintenance_fee"] = v["housing_cost_monthly"]
    for k in ("monthly_rent", "deposit"):
        if v.get(k) is not None:
            offer[k] = v[k]
    return {k: val for k, val in offer.items() if val is not None}
