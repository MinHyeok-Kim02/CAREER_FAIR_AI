# rules/ 스키마

제도 규칙은 모두 JSON 데이터로 두고, 엔진(`engine/eligibility.py`)은 이 문서에 정의된 형식만 해석한다.
제도의 숫자나 지역 분류가 바뀌면 JSON·CSV만 고치고, 새 **입력 필드**나 새 **계산 방식**이 필요할 때만 코드를 고친다.

## 파일 구성

| 파일 | 내용 |
|---|---|
| `programs/*.json` | 제도 1개 = 파일 1개 |
| `payroll_2026.json` | 4대보험 요율, 근로소득세 계산표, 기준 중위소득 (`engine/tax.py` 전용) |
| `assumptions.json` | 제도가 아닌 가정값 (연봉 상승률, 주거비, 이사비 등). 화면에서 변경 가능 |
| `../data/regions.csv` | 시군구별 지역 분류 (근속지원금 등급, 세제 지역 분류) |

## 제도 JSON 공통 필드

| 필드 | 값 | 설명 |
|---|---|---|
| `id` | 문자열 | 파일 이름과 같게 |
| `name` | 문자열 | 화면 표시 이름 |
| `category` | `subsidy` / `tax` / `rent` / `savings` / `one_time` | 결과 화면의 묶음 |
| `status` | `confirmed` / `planned` | 확정 / 예정(정부안, 국회 심의 중 등) |
| `selection` | `entitlement` / `selective` | 요건만 맞으면 지급 / 심사·선정이 있어 확정 아님 |
| `group` | 문자열 | 같은 혜택의 다른 버전(현행·개편안, 일반형·우대형)을 묶음. 그룹당 1개만 적용 |
| `priority` | 정수 | 같은 그룹에서 요건을 만족하는 것 중 큰 값 1개를 적용 |
| `valid.hire_from`, `valid.hire_to` | `YYYY-MM-DD` | 입사일이 이 범위일 때만 적용 |
| `conditions` | 조건 목록 | 모두 만족해야 함 (AND) |
| `benefit` | 계산 방식 | 아래 5가지 중 하나 |
| `sources` | `[{title, url, checked}]` | 원문 출처와 확인일 |
| `note` | 문자열 | 신청 방법, 주의사항 |

## 조건 형식

```json
{"field": "age", "op": "between", "value": [15, 34], "on_unknown": "ask"}
{"any": [[조건, 조건], [조건]]}
```

- `op`: `eq`, `in`, `between`(양끝 포함), `lt`, `lte`, `gt`, `gte`
- `any`: 안쪽 목록 중 하나라도 전부 만족하면 참 (OR of AND)
- `on_unknown`: 입력값이 없을 때
  - 생략: 조건 불충족으로 처리
  - `ask`: 사용자에게 질문 (답이 없으면 결과에 "확인 필요")
  - `warn`: 충족으로 계산하되 결과에 "확인 필요" 표시

## 계산 방식 (`benefit.type`)

| type | 필드 | 계산 |
|---|---|---|
| `schedule` | `table` + `amount_by` 또는 `amount`, `at_months` | 근속 `at_months` 개월 차마다 금액 지급 |
| `monthly` | `base_field`, `cap`, `months` | min(입력값, cap) × months |
| `tax_reduction` | `rate`, `annual_cap`, `years` 또는 `years_by` + `table` | 해마다 min(소득세 × rate, annual_cap), 기간 동안 |
| `match` | `base_field`, `monthly_cap`, `rate`, `months` | min(월 납입액, monthly_cap) × rate × months |
| `one_time` | `amount`, `at_month` | 1회 지급 |

`benefit.uncertain`(선택): 원문에 없어 가정한 부분. 결과에 "확인 필요"로 함께 표시한다.

## 입력 필드

**사용자 입력 / 오퍼 파싱**: `age`, `employment_type`(정규직/계약직), `company_size`(대/중견/중소), `in_industrial_complex`, `company_participating`, `residence_type`(본가/자취/기숙사), `homeless`, `parent_household_median_ratio`, `monthly_rent`, `deposit`, `saving_monthly`, `hire_date`, `salary`

**지역 (regions.csv에서 조회)**: `work_region.{code, sido, sigungu, capital, leap_grade, tax_tier}`, `residence_region.{...}` (자취·기숙사면 근무지와 같다고 가정, 본가면 출발 지역)

**엔진이 계산**:
- `income_annual` = 연봉
- `income_median_ratio` = (연봉 / 12) / 기준 중위소득(가구원 수) × 100
- `relocating` = 출발 지역과 거주 지역의 시도가 다름
- `housing_trade_amount` = 보증금 + 월세 × 100

## 결과 표시 규칙

1. **실질소득 합계**: `status=confirmed`, `selection=entitlement`, `category≠savings`
2. **"개편안 적용" 토글**: 켜면 `status=planned`도 후보에 넣고, 같은 그룹에서는 `priority`가 높은 쪽이 적용됨
3. **선정 시 추가**: `selection=selective` → 합계에서 빼고 따로 표시
4. **저축 시 추가**: `category=savings` → 합계에서 빼고 따로 표시
5. `on_unknown`이나 `benefit.uncertain`이 걸린 항목은 "확인 필요" 표시

## 제도 추가 방법

1. 계산 방식이 위 5가지 중 하나이고 입력 필드가 위 목록 안에 있으면 → `programs/`에 JSON 파일만 추가
2. 새 입력 필드가 필요하면 → 이 문서와 입력 단계(질문·파싱)에 필드 추가
3. 새 계산 방식이 필요하면 → `engine/eligibility.py`에 type 추가

## data/regions.csv

| 열 | 값 | 설명 |
|---|---|---|
| `sido` | 서울, 경기, 경북 … (짧은 이름) | 시도 |
| `sigungu` | 시군구 이름, 또는 `*` | `*`는 그 시도의 기본값 |
| `capital` | true / false | 행정구역상 수도권 여부 |
| `leap_grade` | 수도권 / 일반 / 우대 / 특별 | 청년일자리도약장려금 근속지원금 지역 구분 |
| `tax_tier` | I / II / III / IV | 2026 세제개편안 지역 분류 (수도권 / 비수도권 광역시 등 / 기타 비수도권 / 우대지역) |
| `tax_tier_confirmed` | true / false | false면 시행령 확정 전 잠정값 → 결과에 "확인 필요" |
| `lawd_codes` | `;`로 구분한 5자리 코드 | 전월세 실거래가 API 조회용 (데모 지역만) |
| `note` | 문자열 | 비고 |

**조회 규칙**: (시도, 시군구)가 정확히 일치하는 행을 먼저 쓰고, 없으면 (시도, `*`) 행을 쓴다.
고성군처럼 이름이 같은 시군구가 있으므로 항상 시도와 함께 조회한다.

**출처**
- 근속지원금 우대지원지역 44곳·특별지원지역 40곳: 정책브리핑 카드뉴스(2025-12-16)를 옮긴 2차 자료(braindetox.kr, 2026-11-20 확인). 개수와 수도권 예외 4개 군(강화·옹진·가평·연천)은 경기신문 보도와 일치. 고용노동부 2026 사업운영지침 원문 대조 필요.
- 세제 지역 분류 I~IV: 정책브리핑 2026년 세제개편안. 시군구 목록은 시행령에서 확정 예정이라 모두 잠정값.
