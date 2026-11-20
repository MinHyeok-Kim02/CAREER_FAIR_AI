import streamlit as st
from openai import OpenAI

st.title("진짜연봉 - 배포 테스트")
if st.button("LLM 연결 테스트"):
    client = OpenAI(api_key=st.secrets["LLM_API_KEY"],
                    base_url=st.secrets["LLM_BASE_URL"])
    r = client.chat.completions.create(model=st.secrets["LLM_MODEL"],
        messages=[{"role": "user", "content": "한 문장으로 인사해줘"}])
    st.success(r.choices[0].message.content)
    st.caption("AI가 생성한 답변입니다.")
st.caption("공공데이터 키: " + ("등록됨" if "DATA_GO_KR_KEY" in st.secrets else "없음"))