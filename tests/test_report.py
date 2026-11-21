"""질문·설명 단계 검증 (네트워크 없이)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.llm import FakeLLM, LLMUnavailable                           # noqa: E402
from agent.questions import follow_up, missing_required                # noqa: E402
from agent.report import build_facts, explain, humanize_reason, template_text   # noqa: E402
from engine.compare import evaluate_offer                              # noqa: E402
from engine.eligibility import load_assumptions, load_programs        # noqa: E402
from engine.region import RegionTable                                  # noqa: E402
from engine.tax import load_payroll                                    # noqa: E402

KW = dict(regions=RegionTable(), payroll=load_payroll(), assumptions=load_assumptions(), programs=load_programs())
PERSONAS = json.load(open(ROOT / "samples" / "personas.json", encoding="utf-8"))["personas"]


def _pair(i):
    per = PERSONAS[i]
    return per, [evaluate_offer(o["name"], per["profile"], o, **KW) for o in per["offers"]]


def test_missing_required():
    assert missing_required({"salary": 1, "sido": "서울", "residence_type": "자취"}) == \
        ["company_size", "employment_type", "hire_date", "monthly_rent", "deposit"]


def test_follow_up_impact():
    """P4 구미 중소: 참여기업 여부에 따라 2년 1,440만 원이 달라진다 (EXPECTED.md 2절)."""
    per, (a, _) = _pair(3)
    qs = follow_up(a, per["profile"], per["offers"][0], KW)
    assert [(q["field"], q["impact_24m"]) for q in qs] == [("company_participating", 14_400_000)]


def test_template_uses_engine_numbers():
    _, (a, b) = _pair(0)
    f = build_facts(a, b)
    assert f["앞선오퍼"] == "구미 산단 중견" and f["차이"] == "1,776만 원"
    assert "1,776만 원" in template_text(f)


def test_llm_text_with_new_numbers_is_rejected():
    _, (a, b) = _pair(0)
    f = build_facts(a, b)
    ok = explain(f, FakeLLM(["2년 기준 구미 쪽이 1,776만 원 많습니다. 주거비 차이가 1,548만 원입니다."]))
    assert ok.source == "llm"
    bad = explain(f, FakeLLM(["구미가 약 1,800만 원, 월 75만 원 이득입니다."]))
    assert bad.source == "template" and bad.rejected_numbers == ["1800", "75"]


def test_llm_down_uses_template():
    class Down:
        def chat(self, *a, **k):
            raise LLMUnavailable("down")
    _, (a, b) = _pair(0)
    assert explain(build_facts(a, b), Down()).source == "template"


def test_humanize_reason():
    assert humanize_reason("income_median_ratio=130.2 (조건 lte 60)") == "소득(기준 중위소득 대비 %): 130% (조건: 60% 이하여야 함)"
    assert humanize_reason("company_size='중견' (조건 eq '중소')") == "회사 규모: 중견 (조건: 중소 이어야 함)"


if __name__ == "__main__":
    for n, fn in dict(globals()).items():
        if n.startswith("test_"):
            fn()
    print("질문·설명 검증 통과")
