"""오퍼 파서 검증 (네트워크 없이). 실제 Gemini 정확도는 배포 앱의 '파싱 정확도 확인'에서 본다."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.llm import FakeLLM, LLMUnavailable            # noqa: E402
from agent.parser import parse_offer, to_engine_offer     # noqa: E402
from engine.region import RegionTable                     # noqa: E402

REGIONS = RegionTable()
OFFERS = ROOT / "samples" / "offers"
GOLD = {k: v for k, v in json.load(open(OFFERS / "gold.json", encoding="utf-8")).items() if not k.startswith("_")}
PERSONAS = json.load(open(ROOT / "samples" / "personas.json", encoding="utf-8"))["personas"]
TODAY = "2026-11-21"


def _text(name):
    return (OFFERS / f"{name}.txt").read_text(encoding="utf-8")


def test_gold_passes_validation_without_warnings():
    """정답값을 LLM 응답으로 넣으면 경고 없이 통과해야 한다 (금액이 원문 숫자와 일치)."""
    for name, g in GOLD.items():
        fake = FakeLLM([json.dumps({"values": g["values"], "evidence": {}}, ensure_ascii=False)])
        p = parse_offer(_text(name), fake, REGIONS, TODAY)
        assert p.warnings == [], (name, p.warnings)
        assert {k: p.values[k] for k in g["values"]} == g["values"], name   # bonus_note 등 서술형은 제외


def test_parsed_offer_matches_persona():
    """오퍼레터 → 파싱 → 엔진 입력이 페르소나에 직접 적은 오퍼와 같아야 한다."""
    personas = {(p["id"], o["name"]): o for p in PERSONAS for o in p["offers"]}
    for name, g in GOLD.items():
        engine_offer = to_engine_offer(g["values"], g["answers"])
        expected = dict(personas[tuple(g["persona"])])
        for k in ("name",):
            engine_offer.pop(k, None), expected.pop(k, None)
        expected.setdefault("sigungu", "*")
        assert engine_offer == expected, (name, engine_offer, expected)


def test_wrong_salary_is_flagged():
    g = dict(GOLD["p1_seoul_sme"]["values"], salary=42_000_000)
    p = parse_offer(_text("p1_seoul_sme"), FakeLLM([json.dumps({"values": g})]), REGIONS, TODAY)
    assert any("원문 숫자" in w for w in p.warnings)


def test_invalid_values_are_cleared():
    g = dict(GOLD["p2_gumi_sme"]["values"], company_size="스타트업", sido="화성", hire_date="내년 1월")
    p = parse_offer(_text("p2_gumi_sme"), FakeLLM([json.dumps({"values": g})]), REGIONS, TODAY)
    assert p.values["company_size"] is None and p.values["sido"] is None and p.values["hire_date"] is None
    assert len(p.warnings) == 3


def test_guess_without_grounding_is_cleared():
    """원문에 산단 언급이 없는데 AI가 '산업단지 안'이라고 추측하면 비운다 (11/22 실측 오류 사례)."""
    g = dict(GOLD["p4_gumi_sme"]["values"], in_industrial_complex=True)
    p = parse_offer(_text("p4_gumi_sme"), FakeLLM([json.dumps({"values": g})]), REGIONS, TODAY)
    assert p.values["in_industrial_complex"] is None
    assert any("원문 근거" in w for w in p.warnings)


def test_rule_fallback_when_llm_down():
    class Down:
        def chat_json(self, *a, **k):
            raise LLMUnavailable("down")
    for name, g in GOLD.items():
        p = parse_offer(_text(name), Down(), REGIONS, TODAY)
        assert p.source == "rule"
        assert p.values["salary"] == g["values"]["salary"], name
        assert p.values["sido"] == g["values"]["sido"], name


if __name__ == "__main__":
    for n, fn in dict(globals()).items():
        if n.startswith("test_"):
            fn()
    print("파서 검증 통과")
