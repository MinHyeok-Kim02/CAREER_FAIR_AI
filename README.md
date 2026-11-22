# 진짜연봉 — 취업 오퍼를 '실제로 남는 돈'으로 비교하는 AI 에이전트

> 2026 대한민국 청년 CAREER FAIR AI 해커톤 · 팀 **구미청년** (개인 참가)
> 배포: **https://careerfair.streamlit.app/**

연봉 숫자만으로는 두 오퍼를 공정하게 비교할 수 없습니다. 같은 연봉이라도 근무 지역·회사 규모에 따라
받을 수 있는 정부 지원(근속지원금, 소득세 감면, 월세 지원)과 주거비가 크게 달라지기 때문입니다.
진짜연봉은 처우 안내문을 붙여 넣으면 **세후 연봉 + 제도 혜택 − 주거비 − 이사 비용**을 1·2·5년 단위로 계산해 비교합니다.
수도권 → 지방, 지방 → 수도권 양방향 모두 다룹니다.

## 동작 방식

```
오퍼 원문 ─▶ ① AI 추출 (Gemini) ─▶ ② 코드 검증 ─▶ ③ 확인·추가 질문 ─▶ ④ 규칙 엔진 계산 ─▶ ⑤ AI 설명 ─▶ ⑥ 숫자 대조
             값 옮기기만, 모르면 null   원문 금액 대조     '모름'은 합계 제외      모든 금액 계산       사실 목록만 사용      엔진 값과 다르면 폐기
```

- **AI가 하는 일**: 형식이 제각각인 오퍼 문서(공문·이메일·메신저·표)에서 항목 읽기, 결과를 쉬운 말로 설명하기
- **AI가 하지 않는 일**: 금액 계산. 모든 금액은 `engine/`의 규칙 엔진이 계산하고, 설명문 속 숫자가 엔진 값과 하나라도 다르면 AI 문장을 버리고 규칙 기반 문장을 보여 줍니다.
- 정보가 부족하면 질문하고, 답에 따라 금액이 얼마나 달라지는지(예: "참여 기업이면 2년 최대 1,440만 원") 함께 보여 줍니다.

## 반영 제도 (`rules/programs/`, 2026-11-22 확인)

| 제도 | 상태 | 결과 표시 |
|---|---|---|
| 청년일자리도약장려금 청년 근속지원금 (2026) | 확정 | 합계 포함 |
| 청년 근속지원금 (2027 예산안) | 예정 | 2027년 이후 입사 시 자동 적용 |
| 중소기업 취업 청년 소득세 감면 (현행) | 확정 | 합계 포함 |
| 소득세 감면 지방 차등 연장 (2026 세제개편안) | 예정 | 2027년 이후 입사 시 자동 적용 |
| 청년월세 지원 (국토교통부) | 확정 | 합계 포함 |
| 서울시 청년월세지원 · 청년 중개보수·이사비 지원 | 확정 (선정형) | 선정 시 추가로 따로 표시 |
| 청년미래적금 (일반형·우대형, 2027 개편안) | 확정 / 예정 | 저축 시 추가로 따로 표시 |

제도 규칙은 모두 JSON 데이터이며 형식은 [`rules/SCHEMA.md`](rules/SCHEMA.md)에 정리했습니다.
숫자나 지역 분류가 바뀌면 JSON·CSV만 고치면 되고, 엔진 코드는 바뀌지 않습니다.

## 폴더 구조

```
app.py                 Streamlit 화면 (내 정보 → 오퍼 입력 → 확인·수정 → 결과)
agent/                 LLM이 관여하는 부분
  llm.py               OpenAI 호환 클라이언트 (재시도, 예비 제공자, JSON 응답)
  parser.py            오퍼 원문 → 항목 추출 + 원문 금액 대조·값 검증, LLM 장애 시 규칙 추출
  questions.py         부족한 정보 질문과 답에 따른 영향 금액
  report.py            결과 설명 + 설명 속 숫자 검증
engine/                규칙 엔진 (LLM 없음)
  eligibility.py       제도 조건 판정, 그룹별 선택, 월별 지급액
  tax.py               4대보험·근로소득세 (연말정산 근사)
  compare.py           기간별 실질소득
  region.py, rent.py   지역 분류, 월세 기준값
rules/                 제도 JSON, 세율·요율, 가정값, 스키마 문서
data/                  regions.csv (지역 분류), rent_cache.csv (월세 기준값)
scripts/               build_rent_cache.py (국토교통부 API → rent_cache.csv)
samples/               가상 페르소나 4명, 가상 오퍼레터 8종과 정답값
tests/                 검증 코드와 계산 근거표 (EXPECTED.md)
```

## 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

`.streamlit/secrets.toml` (저장소에 올리지 않음, `.gitignore` 대상):

```toml
LLM_API_KEY = "발급받은 키"
LLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
LLM_MODEL = "gemini-3.5-flash-lite"
DATA_GO_KR_KEY = "공공데이터포털 인증키 (월세 캐시를 새로 만들 때만 필요)"
# 선택: 예비 LLM — LLM_FALLBACK_API_KEY / LLM_FALLBACK_BASE_URL / LLM_FALLBACK_MODEL
```

LLM 키가 없어도 앱은 동작합니다 (규칙 추출 + 규칙 기반 설명).

월세 기준값 갱신: `python scripts/build_rent_cache.py` → `data/rent_cache.csv`

## 검증

```bash
python tests/test_personas.py     # 페르소나 4명 x 오퍼 2개의 1·2·5년 결과표 출력 + 검증
python tests/test_parser.py       # 오퍼 파싱: 원문 금액 대조, 잘못된 값 제거, LLM 장애 시 규칙 추출
python tests/test_report.py       # 질문 영향 금액, 설명문 숫자 검증
python tests/test_agent_basics.py # LLM 클라이언트 재시도·예비 제공자, 월세 데이터 파싱
```

- 계산 근거: [`tests/EXPECTED.md`](tests/EXPECTED.md) — 세금 1건은 줄 단위 손계산, 제도 혜택·주거비는 손계산 기대값
- 실제 Gemini 오퍼 파싱: 가상 오퍼 8종 × 15개 항목 **120/120 일치** (2026-11-22, 앱 사이드바 '점검 도구'로 재현 가능)
- 배포 앱에서 페르소나 4명의 2년 결과가 기대값과 일치함을 확인 (2026-11-21)

## 한계

- 식비·교통비 등 생활비 차이는 반영하지 않습니다.
- 세금은 1인 가구 연말정산 기준 근사치입니다 (월별 원천징수와 차이 가능).
- 2027년 예산안·2026년 세제개편안은 국회 심의 중인 정부안입니다. 확정 내용이 다르면 JSON을 고쳐 반영합니다.
- 세제개편안의 지역 분류(I~IV)는 시행령 확정 전이라 잠정값입니다.
- 회사의 도약장려금 참여 여부처럼 문서에 없는 조건은 사용자가 확인해야 합니다 ('확인 필요'로 표시).

## 데이터와 출처

- 월세 기준값: 국토교통부 전월세 실거래가 자료 3종(단독/다가구, 연립다세대, 오피스텔), 공공데이터포털 Open API.
  2026.07~09, 전용 40㎡ 이하 월세 계약의 중앙값 (서울·성남·구미).
- 제도 내용: 각 JSON의 `sources`에 원문 링크와 확인일을 적었습니다 (정책브리핑, 복지로, 고용노동부, 금융위원회, 서울시, 국회 입법예고 등).
- 기준 중위소득: 보건복지부 2026년 고시.
- **샘플 데이터는 모두 가상입니다.** 페르소나·회사·오퍼레터는 지어낸 것이며 실제 개인정보를 쓰지 않았습니다.
  LLM 무료 티어는 입력이 제공자 서비스 개선에 쓰일 수 있으므로 실제 개인정보를 넣지 마세요.

## Credits / Licenses

| 구성 요소 | 용도 | 라이선스·이용 조건 |
|---|---|---|
| [Streamlit](https://github.com/streamlit/streamlit) | 웹 화면, 배포(Community Cloud) | Apache-2.0 |
| [openai-python](https://github.com/openai/openai-python) | OpenAI 호환 API 클라이언트 | Apache-2.0 |
| [requests](https://github.com/psf/requests) | 공공데이터 API 호출 | Apache-2.0 |
| [pandas](https://github.com/pandas-dev/pandas) | 결과 표 | BSD-3-Clause |
| Google Gemini API (`gemini-3.5-flash-lite`) | 오퍼 추출, 결과 설명 | Google 서비스 약관 |
| 국토교통부 전월세 실거래가 API | 월세 기준값 | 공공데이터포털 이용 조건 |

### AI 활용 표기
- 서비스 기능: Google Gemini (오퍼 항목 추출, 결과 설명). 금액 계산에는 쓰지 않습니다.
- 개발: 코드·문서 초안 대부분은 Anthropic Claude로 작성했습니다. 제도 조사·설계 결정·실행·배포·결과 검증은 팀(본인)이 했습니다.
  자세한 내용은 제출한 'AI 활용내역'에 적었습니다.
