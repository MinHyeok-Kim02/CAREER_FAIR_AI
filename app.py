import streamlit as st

from agent.llm import LLMClient, LLMUnavailable
from engine.rent import load_cache

st.title("진짜연봉 - 배포 테스트")

if st.button("LLM 연결 테스트"):
    try:
        llm = LLMClient()
        text = llm.chat([{"role": "user", "content": "한 문장으로 인사해줘"}])
        st.success(text)
        data = llm.chat_json([{"role": "user", "content":
                               '다음 문장에서 연봉(원)과 근무지를 JSON {"salary": 정수, "city": 문자열}로: '
                               '"연봉 3,600만원, 근무지 경북 구미"'}])
        st.json(data)
        st.caption(f"AI가 생성한 답변입니다. (제공자: {llm.last_provider})")
    except LLMUnavailable as e:
        st.error(str(e))

st.caption("공공데이터 키: " + ("등록됨" if "DATA_GO_KR_KEY" in st.secrets else "없음"))
cache = load_cache()
st.caption(f"월세 캐시: {len(cache)}개 지역" if cache else "월세 캐시: 없음 (scripts/build_rent_cache.py 실행 필요)")

# ---------------------------------------------------------------- 임시 점검: 실제 LLM 오퍼 파싱 정확도
import json                                    # noqa: E402
from datetime import date                      # noqa: E402
from pathlib import Path                       # noqa: E402

from agent.parser import parse_offer           # noqa: E402
from engine.region import RegionTable          # noqa: E402

if st.button("오퍼 파싱 정확도 확인 (가상 오퍼 4종)"):
    offers_dir = Path(__file__).parent / "samples" / "offers"
    gold = {k: v for k, v in json.loads((offers_dir / "gold.json").read_text(encoding="utf-8")).items()
            if not k.startswith("_")}
    regions, llm = RegionTable(), LLMClient()
    total = correct = 0
    for name, g in gold.items():
        p = parse_offer((offers_dir / f"{name}.txt").read_text(encoding="utf-8"), llm, regions, "2026-11-21")
        rows = []
        for k, want in g["values"].items():
            got = p.values.get(k)
            ok = got == want or (k == "company_name" and want and got and want.replace("(주)", "") in str(got))
            total, correct = total + 1, correct + ok
            rows.append({"항목": k, "정답": str(want), "추출": str(got), "일치": "O" if ok else "X"})
        st.subheader(f"{name} ({p.source})")
        st.dataframe(rows, hide_index=True)
        for w in p.warnings:
            st.warning(w)
    st.success(f"전체 {total}개 항목 중 {correct}개 일치 ({correct / total:.0%})")
    st.caption("AI가 추출한 값입니다. 가상 문서만 사용합니다.")
