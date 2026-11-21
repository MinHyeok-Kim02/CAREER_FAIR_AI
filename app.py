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
