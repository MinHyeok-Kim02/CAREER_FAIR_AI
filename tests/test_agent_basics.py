"""LLM 클라이언트·월세 파싱 기본 동작 (네트워크 없이)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.llm as llm                        # noqa: E402
from engine.rent import lookup, parse_items, summarize, RentRef   # noqa: E402

SAMPLE = """<response><header><resultCode>000</resultCode></header><body><items>
<item><deposit>1,000</deposit><monthlyRent>55</monthlyRent><excluUseAr>23.1</excluUseAr></item>
<item><deposit>500</deposit><monthlyRent>45</monthlyRent><excluUseAr>19.8</excluUseAr></item>
<item><deposit>30,000</deposit><monthlyRent>0</monthlyRent><excluUseAr>30</excluUseAr></item>
<item><보증금액>2000</보증금액><월세금액>70</월세금액><전용면적>59</전용면적></item>
</items><totalCount>4</totalCount></body></response>"""


def test_extract_json():
    assert llm._extract_json('설명\n```json\n{"a": 1}\n```') == {"a": 1}
    assert llm._extract_json('앞 {"b": [1, 2]} 뒤') == {"b": [1, 2]}


def test_fallback_then_unavailable():
    llm.BACKOFF_SEC = (0, 0)
    dead = "http://127.0.0.1:9/v1"
    c = llm.LLMClient([llm.Provider("primary", "k", dead, "m"), llm.Provider("fallback", "k", dead, "m")])
    try:
        c.chat([{"role": "user", "content": "hi"}])
        assert False, "실패해야 함"
    except llm.LLMUnavailable as e:
        assert "primary" in str(e) and "fallback" in str(e)


def test_rent_parse_and_summary():
    rows, total = parse_items(SAMPLE)
    assert total == 4 and rows[0] == {"deposit": 10_000_000, "monthly_rent": 550_000, "area": 23.1}
    # 전세(월세 0)와 40㎡ 초과는 빼고 중앙값
    assert summarize(rows) == (500_000, 7_500_000, 2)


def test_rent_lookup_fallback():
    cache = {("서울", "*"): RentRef(600_000, 10_000_000, 100, "202607-202609", "2026-11-21")}
    assert lookup(cache, "서울", "마포구").monthly_rent == 600_000
    assert lookup(cache, "경북", "구미시") is None


if __name__ == "__main__":
    for name, fn in dict(globals()).items():
        if name.startswith("test_"):
            fn()
    print("기본 동작 검증 통과")
