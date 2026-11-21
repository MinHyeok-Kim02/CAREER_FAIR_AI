"""진짜연봉 — 취업 오퍼를 실질소득으로 비교하는 AI 에이전트.

AI(LLM): 오퍼 원문 읽기(parser), 결과 설명(report)
규칙 엔진: 제도 판정·세금·주거비 등 모든 금액 계산(engine) — AI가 만든 숫자는 화면에 내보내지 않는다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from agent.llm import LLMClient, LLMUnavailable, NoLLM
from agent.parser import parse_offer, to_engine_offer
from agent.questions import QUESTIONS, follow_up, missing_required
from agent.report import build_facts, explain, humanize_reason, man
from engine import rent
from engine.compare import evaluate_offer, planned_default
from engine.eligibility import load_assumptions, load_programs
from engine.region import RegionTable
from engine.tax import load_payroll

ROOT = Path(__file__).parent
SIDOS = ["서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종",
         "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
YES_NO = {"예": True, "아니오": False, "모름": None}
PERIODS = {"1년": 12, "2년": 24, "5년": 60}

st.set_page_config(page_title="진짜연봉", page_icon="💰", layout="wide")


@st.cache_resource
def tables():
    return dict(regions=RegionTable(), payroll=load_payroll(), programs=load_programs()), rent.load_cache()


@st.cache_resource
def llm():
    try:
        return LLMClient()
    except LLMUnavailable:
        return NoLLM()


BASE, RENT = tables()
SAMPLES = {k: v for k, v in json.loads((ROOT / "samples/offers/gold.json").read_text(encoding="utf-8")).items()
           if not k.startswith("_")}
SAMPLE_LABEL = {"p1_gumi_midsize": "구미 산단 중견 (공문)", "p1_seoul_sme": "서울 중소 (이메일)",
                "p2_seoul_sme": "서울 중소 (이메일)", "p2_gumi_sme": "구미 중소 (메신저)",
                "p3_seongnam_midsize": "성남 중견 (공문)", "p3_gumi_dorm": "구미 산단 중견·기숙사 (표)",
                "p4_gumi_sme": "구미 중소 (문자)", "p4_seongnam_sme": "성남 중소 (채용 플랫폼)"}
PERSONAS = {p["id"]: p for p in json.loads((ROOT / "samples/personas.json").read_text(encoding="utf-8"))["personas"]}
PRESET_SAMPLES = {"P1": ("p1_gumi_midsize", "p1_seoul_sme"), "P2": ("p2_seoul_sme", "p2_gumi_sme"),
                  "P3": ("p3_seongnam_midsize", "p3_gumi_dorm"), "P4": ("p4_gumi_sme", "p4_seongnam_sme")}
ss = st.session_state
ss.setdefault("parsed", {"A": None, "B": None})


def apply_preset():
    """시연 페르소나를 고르면 내 정보와 예시 오퍼 두 개를 채운다 (모두 가상 데이터)."""
    pid = ss.preset
    if pid not in PERSONAS:
        return
    prof = PERSONAS[pid]["profile"]
    ss.age, ss.home_sido, ss.home_sigungu = prof["age"], prof["home_sido"], prof["home_sigungu"]
    ss.sample_A, ss.sample_B = PRESET_SAMPLES[pid]
    ss.parsed = {"A": None, "B": None}
    ss.pop("result", None)

# ---------------------------------------------------------------- 사이드바: 가정값
with st.sidebar:
    st.header("계산 가정")
    assumptions = load_assumptions()
    assumptions["salary_growth_rate"]["value"] = st.slider("연봉 상승률(연)", 0.0, 0.08, 0.03, 0.005, format="%.3f")
    assumptions["saving_monthly"]["value"] = st.number_input("청년미래적금 월 납입액", 0, 500_000, 500_000, 50_000)
    assumptions["housing"]["자취"]["maintenance_fee"] = st.number_input("자취 관리비(월)", 0, 300_000, 100_000, 10_000)
    planned_mode = st.radio("예정 제도(정부안)", ["입사일 기준 자동", "적용", "미적용"],
                            help="2027년 이후 입사면 자동으로 적용합니다. 정부안은 국회·시행령 확정 전이라 바뀔 수 있습니다.")
    st.caption("생활비(식비·교통비) 차이는 반영하지 않습니다.")

st.title("진짜연봉")
st.write("연봉 숫자가 아니라 **세금·지원금·주거비까지 뺀 실제로 남는 돈**으로 두 오퍼를 비교합니다.")

# ---------------------------------------------------------------- ① 내 정보
st.selectbox("시연용 가상 페르소나", ["(직접 입력)"] + list(PERSONAS), key="preset", on_change=apply_preset,
             format_func=lambda k: PERSONAS[k]["name"] if k in PERSONAS else k)
st.subheader("① 내 정보")
c1, c2, c3, c4 = st.columns(4)
ss.setdefault("age", 25)
ss.setdefault("home_sido", "경북")
ss.setdefault("home_sigungu", "구미시")
age = c1.number_input("만 나이", 15, 45, key="age")
home_sido = c2.selectbox("지금 사는 곳(시도)", SIDOS, key="home_sido")
home_sigungu = c3.text_input("시군구", key="home_sigungu")
homeless = c4.selectbox("무주택", list(YES_NO), index=0)
profile = {"age": int(age), "home_sido": home_sido, "home_sigungu": home_sigungu or "*",
           "homeless": YES_NO[homeless]}

# ---------------------------------------------------------------- ② 오퍼 입력
st.subheader("② 오퍼 입력")
st.caption("처우 안내 메일·문자·문서를 붙여 넣으면 AI가 필요한 항목을 읽어 옵니다. **실제 개인정보는 넣지 마세요** (시연은 가상 오퍼 사용).")
cols = st.columns(2)
for slot, col in zip("AB", cols):
    with col:
        ss.setdefault(f"sample_{slot}", "p1_gumi_midsize" if slot == "A" else "p1_seoul_sme")
        sample = st.selectbox(f"오퍼 {slot} 예시 불러오기", ["(직접 입력)"] + list(SAMPLES),
                              format_func=lambda k: SAMPLE_LABEL.get(k, k), key=f"sample_{slot}")
        default = "" if sample == "(직접 입력)" else (ROOT / f"samples/offers/{sample}.txt").read_text(encoding="utf-8")
        text = st.text_area(f"오퍼 {slot} 원문", default, height=220, key=f"text_{slot}_{sample}")
        if st.button(f"AI로 오퍼 {slot} 읽기", key=f"parse_{slot}", use_container_width=True):
            with st.spinner("오퍼를 읽는 중…"):
                ss.parsed[slot] = parse_offer(text, llm(), BASE["regions"], date.today().isoformat())
                ss.pop("result", None)


# ---------------------------------------------------------------- ③ 확인·추가 질문
def edit_offer(slot: str, p) -> dict:
    v = dict(p.values)
    st.markdown(f"**오퍼 {slot}** — {'AI 추출' if p.source == 'llm' else '규칙 추출(AI 미사용)'}")
    for w in p.warnings:
        st.warning(w)
    k = lambda name: f"{slot}_{name}"
    v["company_name"] = st.text_input("회사", v.get("company_name") or f"오퍼 {slot}", key=k("name"))
    v["salary"] = st.number_input("연봉(세전, 원)", 0, 300_000_000, v.get("salary") or 0, 1_000_000, key=k("salary"))
    a, b = st.columns(2)
    v["sido"] = a.selectbox("근무지 시도", SIDOS, index=SIDOS.index(v["sido"]) if v.get("sido") in SIDOS else 0, key=k("sido"))
    v["sigungu"] = b.text_input("근무지 시군구", v.get("sigungu") or "", key=k("sgg")) or None
    sizes = ["중소", "중견", "대", "모름"]
    v["company_size"] = a.selectbox("회사 규모", sizes, index=sizes.index(v.get("company_size") or "모름"), key=k("size"))
    v["company_size"] = None if v["company_size"] == "모름" else v["company_size"]
    v["employment_type"] = b.selectbox("고용 형태", ["정규직", "계약직"],
                                       index=0 if v.get("employment_type") != "계약직" else 1, key=k("emp"))
    hd = date.fromisoformat(v["hire_date"]) if v.get("hire_date") else date(2027, 1, 4)
    v["hire_date"] = a.date_input("입사 예정일", hd, key=k("hire")).isoformat()
    inv = {True: "예", False: "아니오", None: "모름"}
    v["in_industrial_complex"] = YES_NO[b.selectbox("산업단지 안", list(YES_NO), list(YES_NO).index(inv[v.get("in_industrial_complex")]), key=k("ic"))]
    v["company_participating"] = YES_NO[a.selectbox(QUESTIONS["company_participating"].label, list(YES_NO),
                                                    list(YES_NO).index(inv[v.get("company_participating")]),
                                                    help=QUESTIONS["company_participating"].help, key=k("part"))]
    homes = ["본가", "자취", "기숙사"]
    guess = "기숙사" if v.get("housing") in ("기숙사", "사택") else ("본가" if v["sido"] == home_sido else "자취")
    res = b.selectbox(QUESTIONS["residence_type"].label, homes, homes.index(guess), key=k("res"))
    answers = {"residence_type": res}
    if res == "자취":
        ref = rent.lookup(RENT, v["sido"], v.get("sigungu") or "*")
        r1, r2 = st.columns(2)
        answers["monthly_rent"] = r1.number_input("월세(원)", 0, 3_000_000, ref.monthly_rent if ref else 500_000, 10_000, key=k("rent"))
        answers["deposit"] = r2.number_input("보증금(원)", 0, 300_000_000, ref.deposit if ref else 10_000_000, 1_000_000, key=k("dep"))
        st.caption(f"기본값: 국토교통부 전월세 실거래가 중앙값 ({ref.months}, 전용 40㎡ 이하 {ref.samples:,}건)" if ref
                   else "이 지역은 실거래가 기준값이 없어 직접 입력해 주세요.")
    with st.expander("AI가 근거로 삼은 원문"):
        st.json(p.evidence or {"안내": "근거 없음 (규칙 추출)"})
    return to_engine_offer(v, answers)


if ss.parsed["A"] and ss.parsed["B"]:
    st.subheader("③ 읽은 내용 확인·수정")
    st.caption("AI가 읽은 값입니다. 틀린 곳은 고쳐 주세요. '모름'으로 두면 그 조건의 혜택은 합계에서 빼고 '확인 필요'로 따로 보여 줍니다.")
    cols = st.columns(2)
    offers = {}
    for slot, col in zip("AB", cols):
        with col:
            offers[slot] = edit_offer(slot, ss.parsed[slot])
    missing = {s: missing_required(o) for s, o in offers.items()}
    if any(missing.values()):
        for s, m in missing.items():
            if m:
                st.error(f"오퍼 {s}: {', '.join(QUESTIONS[f].label for f in m)} 입력이 필요합니다.")
    elif st.button("실질소득 비교하기", type="primary", use_container_width=True):
        ss.result = offers

# ---------------------------------------------------------------- ④ 결과
if "result" in ss:
    offers = ss.result
    hire = min(o["hire_date"] for o in offers.values())
    include = {"입사일 기준 자동": planned_default(hire), "적용": True, "미적용": False}[planned_mode]
    kw = dict(**BASE, assumptions=assumptions)
    res = {s: evaluate_offer(o["name"], profile, o, include_planned=include, **kw) for s, o in offers.items()}
    st.subheader("④ 결과")
    period_label = st.radio("비교 기간", list(PERIODS), index=1, horizontal=True)
    period = PERIODS[period_label]
    a, b = res["A"], res["B"]

    m1, m2 = st.columns(2)
    for col, r, other in ((m1, a, b), (m2, b, a)):
        p, q = r.periods[period], other.periods[period]
        col.metric(f"{r.name} — {period_label} 실질소득", man(p["real_income"]),
                   delta=man(p["real_income"] - q["real_income"]))
    if include:
        st.info("2027년 이후 입사 기준으로 정부안(예정) 제도를 반영했습니다. 국회·시행령 확정 과정에서 달라질 수 있습니다.")

    rows = {"세후 연봉": "net_salary", "제도 혜택": "benefits", "주거비 (−)": "housing_cost",
            "이사·초기 비용 (−)": "one_time_cost", "실질소득": "real_income",
            "선정 시 추가": "selective", "저축 시 추가 (36개월 만기)": "savings", "확인 필요": "conditional"}
    table = pd.DataFrame({r.name: {k: man(r.periods[period][f]) for k, f in rows.items()} for r in (a, b)})
    st.table(table)
    chart = pd.DataFrame({r.name: {"세후 연봉": r.periods[period]["net_salary"] / 10_000,
                                   "제도 혜택": r.periods[period]["benefits"] / 10_000,
                                   "주거·이사 비용": -(r.periods[period]["housing_cost"] + r.periods[period]["one_time_cost"]) / 10_000}
                          for r in (a, b)}).T
    st.bar_chart(chart, stack=True, y_label="만 원")

    st.markdown("#### 설명")
    facts = build_facts(a, b, period)
    cache_key = json.dumps(facts, ensure_ascii=False, sort_keys=True)
    ss.setdefault("explained", {})
    if cache_key not in ss.explained:                    # 같은 결과면 LLM을 다시 부르지 않는다
        with st.spinner("설명을 쓰는 중…"):
            ss.explained[cache_key] = explain(facts, llm())
    report = ss.explained[cache_key]
    st.write(report.text)
    st.caption("AI가 생성한 설명입니다. 금액은 규칙 엔진 계산값과 대조해 검증했습니다."
               if report.source == "llm" else "규칙 기반 설명입니다 (AI 설명이 검증을 통과하지 못했거나 사용할 수 없음).")

    st.markdown("#### 확인하면 금액이 달라지는 항목")
    any_q = False
    for slot, r in res.items():
        for q in follow_up(r, profile, offers[slot], kw):
            any_q = True
            st.warning(f"**오퍼 {slot}** · {q['question'].label} → 답에 따라 2년 실질소득이 최대 "
                       f"**{man(q['impact_24m'])}** 달라집니다. {q['question'].help}")
    if not any_q:
        st.success("추가로 확인할 조건이 없습니다.")

    st.markdown("#### 제도별 내역")
    for col, r in zip(st.columns(2), (a, b)):
        with col:
            st.markdown(f"**{r.name}**")
            for x in r.benefits:
                tag = {"main": "합계 포함", "selective": "선정 시 추가", "savings": "저축 시 추가",
                       "conditional": "확인 필요"}[x["bucket"]]
                plan = " · 예정" if x["status"] == "planned" else ""
                with st.expander(f"{x['name']} — {man(x['amounts'][period])} ({tag}{plan})"):
                    st.write(x["note"])
                    for u in x["uncertain"]:
                        st.caption("가정: " + u)
                    for s in x["sources"]:
                        st.markdown(f"- [{s['title']}]({s['url']}) (확인 {s['checked']})")
            hidden = [n for n in r.not_applied if n["reasons"] != ["입사일이 적용 기간 밖"]]
            with st.expander(f"해당하지 않는 제도 {len(hidden)}개"):
                for n in hidden:
                    st.markdown(f"- **{n['name']}**: " + "; ".join(humanize_reason(x) for x in n["reasons"][:2]))

    st.caption("실질소득 = 세후 연봉 + 제도 혜택 − 주거비 − 이사·초기 비용. 세금은 연말정산 기준 근사치(1인 가구). "
               "선정형 지원·저축형 지원·확인이 필요한 혜택은 합계에서 빼고 따로 표시합니다.")

# ---------------------------------------------------------------- 점검 도구 (발표·검증용)
with st.sidebar.expander("점검 도구"):
    if st.button("오퍼 파싱 정확도 확인 (가상 4종)"):
        client, total, ok_n = llm(), 0, 0
        for name, g in SAMPLES.items():
            p = parse_offer((ROOT / f"samples/offers/{name}.txt").read_text(encoding="utf-8"), client,
                            BASE["regions"], "2026-11-21")
            for k, want in g["values"].items():
                got = p.values.get(k)
                ok = got == want or (k == "company_name" and want and got and want.replace("(주)", "") in str(got))
                total, ok_n = total + 1, ok_n + ok
                if not ok:
                    st.write(f"{name}.{k}: 정답 {want} / 추출 {got}")
        st.success(f"{ok_n}/{total} 일치 ({ok_n / total:.0%})")
