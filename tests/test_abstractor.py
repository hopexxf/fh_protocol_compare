"""思路 3 摘要生成 — 单元测试（mock LLM，零 token）"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.abstractor import (
    condense_analyzed,
    build_abstract_prompt,
    generate_abstract,
)


def _analyzed():
    return [
        {
            "base_section_title": "U-plane Message Structure",
            "compare_section_title": "U-plane Message Structure",
            "base_section_number": "5.2",
            "compare_section_number": "5.2",
            "base_page": "10",
            "compare_page": "12",
            "llm_result": {
                "diffs": [{
                    "type": "feature-changed",
                    "impact": "高",
                    "base_quote": "x" * 5000,    # 应被浓缩丢弃
                    "compare_quote": "y" * 5000,
                    "description": "eCPRI header 结构变更",
                }],
                "summary": "协议升级",
            },
        },
        {
            "base_section_title": "Scope",
            "llm_result": {"diffs": [], "summary": ""},  # 无 diff，应被跳过
        },
    ]


def test_condense_drops_quotes():
    condensed = condense_analyzed(_analyzed())
    assert "x" * 5000 not in condensed  # 长原文已截断
    assert "eCPRI header" in condensed
    # 标题不出现在位置行，检查格式特征即可
    assert "### [1]" in condensed
    assert "feature-changed" in condensed
    assert "Scope" not in condensed  # 无 diff 的跳过


def test_build_abstract_prompt_structure():
    messages = build_abstract_prompt("清单", {"top_n": 7})
    assert messages[0]["role"] == "system"
    assert "工作量定位" in messages[1]["content"]
    assert "关键差异详述" in messages[1]["content"]
    assert "其余差异简表" in messages[1]["content"]


def test_generate_abstract_mock():
    called = {}

    def fake_llm(messages):
        called["messages"] = messages
        return "## 执行摘要\n关键差异..."

    out = generate_abstract(_analyzed(), {"enabled": True, "top_n": 10}, llm_call=fake_llm)
    assert "执行摘要" in out
    # 浓缩内容已传给 LLM
    assert "eCPRI header" in called["messages"][1]["content"]


def test_generate_abstract_empty():
    out = generate_abstract(
        [{"llm_result": {"diffs": [], "summary": ""}}], {"enabled": True}
    )
    assert "无显著差异" in out
