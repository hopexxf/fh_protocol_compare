"""
test_analyzer.py — Phase 5：LLM 分析模块测试

实际 API（来自 src/analyzer.py）：
  analyze_diff_item(diff_item, llm_client) -> dict
  analyze_diff_batch(diff_results, llm_client) -> list[dict]
  summarize_all(analyzed) -> dict

TC5-1~4：analyze_diff_item（对齐章节/Compare独有/Base独有/无LLM）
TC5-5~8：analyze_diff_batch（批量过滤/跳过无变更/全部分析/并发顺序）
TC5-9~12：summarize_all（统计聚合/空输入/类型分布/影响等级）
TC5-13：异常降级（LLM 返回非 JSON）
TC5-14：Base 独有章节 fallback
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.analyzer import (
    analyze_diff_item,
    analyze_diff_batch,
    summarize_all,
    ANALYZE_DIFF_SYSTEM,
    ANALYZE_DIFF_USER,
)


# ------------------------------------------------------------------
# Mock LLM Client
# ------------------------------------------------------------------

def _make_mock_client(responses: list[str]):
    """构建可迭代返回固定 JSON 的 Mock LLM Client"""
    it = iter(responses)
    mock = MagicMock()
    def fake_chat(messages, **kwargs):
        response = next(it, '{"diffs": [], "summary": "mock fallback"}')
        return response
    mock.chat.side_effect = fake_chat
    return mock


def _json_response(diffs: list, summary: str) -> str:
    return json.dumps({"diffs": diffs, "summary": summary}, ensure_ascii=False)


# ------------------------------------------------------------------
# 测试数据
# ------------------------------------------------------------------

DIFF_ITEM_ALIGNED = {
    "base_section_id": "1",
    "base_section_number": "1",
    "base_section_title": "Overview",
    "compare_section_id": "2",
    "compare_section_number": "2",
    "compare_section_title": "Introduction",
    "similarity": 0.82,
    "has_diff": True,
    "diff_summary": "变更：删除 50 字符，新增 80 字符",
    "base_content": "The U-plane interface defines the data format.",
    "compare_content": "The C-plane interface defines the control signaling for the protocol.",
}

DIFF_ITEM_COMPARE_ONLY = {
    "base_section_id": None,
    "base_section_number": "",
    "base_section_title": "",
    "compare_section_id": "5",
    "compare_section_number": "5",
    "compare_section_title": "New Protocol Extension",
    "similarity": 0,
    "has_diff": True,
    "diff_summary": "Compare 独有章节",
    "base_content": "",
    "compare_content": "This section describes the new protocol extension with enhanced features.",
}

DIFF_ITEM_BASE_ONLY = {
    "base_section_id": "3",
    "base_section_number": "3",
    "base_section_title": "Message Structure",
    "compare_section_id": None,
    "compare_section_number": "",
    "compare_section_title": "",
    "similarity": 0,
    "has_diff": True,
    "diff_summary": "Base 独有章节",
    "base_content": "Control messages are structured as follows.",
    "compare_content": "",
}

DIFF_ITEM_NO_CHANGE = {
    "base_section_id": "10",
    "base_section_number": "10",
    "base_section_title": "Annex A",
    "compare_section_id": "10",
    "compare_section_number": "10",
    "compare_section_title": "Annex A",
    "similarity": 1.0,
    "has_diff": False,
    "diff_summary": "无显著变更",
    "base_content": "Reference documents are listed here.",
    "compare_content": "Reference documents are listed here.",
}


# ------------------------------------------------------------------
# TC5-1：analyze_diff_item 对齐章节
# ------------------------------------------------------------------

class TestAnalyzeDiffItem:
    def test_aligned_section_returns_llm_result(self):
        mock_client = _make_mock_client([
            _json_response([{
                "type": "design-diff",
                "impact": "高",
                "base_quote": "U-plane interface",
                "compare_quote": "C-plane interface",
                "description": "U-plane 接口变更为 C-plane 接口，设计层面有重大变化。",
                "workload_hint": "需要重新设计接口适配层。",
            }], "U-plane 变更为 C-plane，影响较高"),
        ])

        result = analyze_diff_item(DIFF_ITEM_ALIGNED, llm_client=mock_client)

        assert "llm_result" in result
        assert "diffs" in result["llm_result"]
        assert len(result["llm_result"]["diffs"]) == 1
        assert result["llm_result"]["diffs"][0]["impact"] == "高"

    def test_preserve_original_fields(self):
        mock_client = _make_mock_client([_json_response([], "无显著变更")])
        result = analyze_diff_item(DIFF_ITEM_ALIGNED, llm_client=mock_client)

        assert result["base_section_id"] == "1"
        assert result["compare_section_id"] == "2"
        assert result["similarity"] == 0.82

    def test_client_chat_called(self):
        mock_client = _make_mock_client([_json_response([], "无显著差异")])
        analyze_diff_item(DIFF_ITEM_ALIGNED, llm_client=mock_client)
        assert mock_client.chat.called
        call_args = mock_client.chat.call_args
        assert isinstance(call_args[0][0], list)  # messages list
        assert len(call_args[0][0]) == 2  # system + user

    def test_system_prompt_includes_fh_protocol(self):
        mock_client = _make_mock_client([_json_response([], "ok")])
        analyze_diff_item(DIFF_ITEM_ALIGNED, llm_client=mock_client)
        messages = mock_client.chat.call_args[0][0]
        system_content = messages[0]["content"]
        assert "U-plane" in system_content or "C-plane" in system_content


# ------------------------------------------------------------------
# TC5-2：analyze_diff_item Compare 独有章节
# ------------------------------------------------------------------

class TestAnalyzeCompareOnly:
    def test_compare_only_returns_llm_added(self):
        mock_client = _make_mock_client([
            json.dumps({"type": "new-feature", "description": "描述内容"})
        ])

        result = analyze_diff_item(DIFF_ITEM_COMPARE_ONLY, llm_client=mock_client)

        assert "llm_added" in result
        assert result["llm_added"]["type"] == "new-feature"
        assert "description" in result["llm_added"]

    def test_compare_only_uses_correct_prompt(self):
        mock_client = _make_mock_client([
            json.dumps({"type": "new-design", "description": "新设计"})
        ])
        analyze_diff_item(DIFF_ITEM_COMPARE_ONLY, llm_client=mock_client)
        messages = mock_client.chat.call_args[0][0]
        user_content = messages[1]["content"]
        assert "New Protocol Extension" in user_content


# ------------------------------------------------------------------
# TC5-3：analyze_diff_item Base 独有章节（fallback）
# ------------------------------------------------------------------

class TestAnalyzeBaseOnly:
    def test_base_only_returns_feature_removed(self):
        mock_client = MagicMock()  # 不应被调用
        result = analyze_diff_item(DIFF_ITEM_BASE_ONLY, llm_client=mock_client)

        assert "llm_result" in result
        assert result["llm_result"]["diffs"][0]["type"] == "feature-removed"
        assert not mock_client.chat.called

    def test_base_only_impact_medium(self):
        mock_client = MagicMock()
        result = analyze_diff_item(DIFF_ITEM_BASE_ONLY, llm_client=mock_client)
        assert result["llm_result"]["diffs"][0]["impact"] == "中"


# ------------------------------------------------------------------
# TC5-4：analyze_diff_item 无 LLM client
# ------------------------------------------------------------------

class TestAnalyzeNoClient:
    @patch("src.analyzer.get_llm_client")
    def test_no_client_uses_default(self, mock_get_client):
        mock_client = _make_mock_client([_json_response([], "ok")])
        mock_get_client.return_value = mock_client
        result = analyze_diff_item(DIFF_ITEM_ALIGNED)
        assert mock_get_client.called
        assert "llm_result" in result


# ------------------------------------------------------------------
# TC5-5：analyze_diff_batch 批量分析
# ------------------------------------------------------------------

class TestAnalyzeBatch:
    def test_batch_returns_list_of_same_length(self):
        mock_client = _make_mock_client([
            _json_response([], "无变更"),
            _json_response([{"type": "feature-added", "impact": "低",
                             "base_quote": "", "compare_quote": "", "description": "新增", "workload_hint": "注意"}], "有变更"),
            _json_response([], "无变更"),
        ])
        diffs = [DIFF_ITEM_ALIGNED, DIFF_ITEM_COMPARE_ONLY, DIFF_ITEM_NO_CHANGE]
        results = analyze_diff_batch(diffs, llm_client=mock_client)

        assert len(results) == 3

    def test_batch_skips_no_diff_aligned_sections(self):
        mock_client = _make_mock_client([
            _json_response([], "无变更"),
        ])
        # 仅 DIFF_ITEM_NO_CHANGE 应跳过 LLM（has_diff=False + 有 compare）
        diffs = [DIFF_ITEM_NO_CHANGE]
        results = analyze_diff_batch(diffs, llm_client=mock_client)

        assert len(results) == 1
        assert results[0]["llm_result"]["summary"] == "无显著变更"
        # 无需调用 LLM
        assert mock_client.chat.call_count == 0

    def test_batch_all_unanalyzed_items_get_llm(self):
        mock_client = _make_mock_client([
            _json_response([], "变更1"),
            _json_response([{"type": "feature-added", "impact": "低",
                             "base_quote": "", "compare_quote": "", "description": "变更2", "workload_hint": ""}], "变更2"),
            _json_response([{"type": "feature-removed", "impact": "中",
                             "base_quote": "", "compare_quote": "", "description": "变更3", "workload_hint": ""}], "变更3"),
        ])
        diffs = [DIFF_ITEM_ALIGNED, DIFF_ITEM_COMPARE_ONLY, DIFF_ITEM_BASE_ONLY]
        results = analyze_diff_batch(diffs, llm_client=mock_client)

        assert mock_client.chat.call_count == 2  # Base 独有不调用 LLM
        # 三个都应有结果
        assert all("llm_result" in r or "llm_added" in r for r in results)

    def test_batch_preserves_order(self):
        mock_client = _make_mock_client([
            _json_response([], "first"),
            _json_response([], "second"),
            _json_response([], "third"),
        ])
        diffs = [DIFF_ITEM_ALIGNED, DIFF_ITEM_COMPARE_ONLY, DIFF_ITEM_BASE_ONLY]
        results = analyze_diff_batch(diffs, llm_client=mock_client)

        assert results[0]["base_section_id"] == "1"
        assert results[1]["compare_section_id"] == "5"
        assert results[2]["base_section_id"] == "3"


# ------------------------------------------------------------------
# TC5-6：summarize_all 统计聚合
# ------------------------------------------------------------------

class TestSummarizeAll:
    def test_summarize_basic_stats(self):
        analyzed = [
            {**DIFF_ITEM_ALIGNED, "llm_result": {
                "diffs": [
                    {"type": "design-diff", "impact": "高"},
                    {"type": "param-diff", "impact": "中"},
                ],
                "summary": "有设计变更",
            }},
            {**DIFF_ITEM_NO_CHANGE, "llm_result": {"diffs": [], "summary": "无变更"}},
        ]
        stats = summarize_all(analyzed)

        assert stats["total_sections"] == 2
        assert stats["sections_with_diff"] == 1
        assert stats["total_diff_items"] == 2

    def test_summarize_by_type(self):
        analyzed = [
            {**DIFF_ITEM_ALIGNED, "llm_result": {
                "diffs": [
                    {"type": "design-diff", "impact": "高"},
                    {"type": "design-diff", "impact": "中"},
                    {"type": "feature-added", "impact": "低"},
                ],
                "summary": "",
            }},
        ]
        stats = summarize_all(analyzed)
        assert stats["by_type"]["design-diff"] == 2
        assert stats["by_type"]["feature-added"] == 1

    def test_summarize_by_impact(self):
        analyzed = [
            {**DIFF_ITEM_ALIGNED, "llm_result": {
                "diffs": [
                    {"type": "design-diff", "impact": "高"},
                    {"type": "param-diff", "impact": "中"},
                    {"type": "feature-added", "impact": "低"},
                ],
                "summary": "",
            }},
        ]
        stats = summarize_all(analyzed)
        assert stats["by_impact"]["高"] == 1
        assert stats["by_impact"]["中"] == 1
        assert stats["by_impact"]["低"] == 1

    def test_summarize_empty_input(self):
        stats = summarize_all([])
        assert stats["total_sections"] == 0
        assert stats["total_diff_items"] == 0
        assert stats["sections_with_diff"] == 0

    def test_summarize_unknown_impact(self):
        analyzed = [
            {**DIFF_ITEM_ALIGNED, "llm_result": {
                "diffs": [{"type": "design-diff", "impact": "严重"}],
                "summary": "",
            }},
        ]
        stats = summarize_all(analyzed)
        assert stats["by_impact"]["严重"] == 1


# ------------------------------------------------------------------
# TC5-7：异常降级
# ------------------------------------------------------------------

class TestAnalyzeExceptionHandling:
    def test_non_json_response_fallback(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = "这不是 JSON 格式的响应"

        result = analyze_diff_item(DIFF_ITEM_ALIGNED, llm_client=mock_client)

        assert "llm_result" in result
        assert result["llm_result"]["diffs"] == []
        assert "summary" in result["llm_result"]

    def test_incomplete_json_fallback(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = '{"diffs": [{"type": "design-diff"}'  # 不完整

        result = analyze_diff_item(DIFF_ITEM_ALIGNED, llm_client=mock_client)

        assert "llm_result" in result
        assert result["llm_result"]["diffs"] == []

    def test_missing_keys_fallback(self):
        mock_client = MagicMock()
        mock_client.chat.return_value = '{"only": "some keys"}'

        result = analyze_diff_item(DIFF_ITEM_ALIGNED, llm_client=mock_client)

        # 缺少必需 keys，降级到原始响应摘要
        assert "llm_result" in result
        assert result["llm_result"]["diffs"] == []

    def test_llm_exception_continues(self):
        mock_client = MagicMock()
        mock_client.chat.side_effect = RuntimeError("LLM 网络错误")

        result = analyze_diff_item(DIFF_ITEM_ALIGNED, llm_client=mock_client)

        assert "llm_result" in result
        assert "LLM 调用失败" in result["llm_result"]["summary"]


# ------------------------------------------------------------------
# TC5-8：Prompt 模板验证
# ------------------------------------------------------------------

class TestPromptTemplates:
    def test_diff_system_prompt_has_uplane(self):
        assert "U-plane" in ANALYZE_DIFF_SYSTEM or "前传" in ANALYZE_DIFF_SYSTEM

    def test_diff_user_prompt_has_base_compare_sections(self):
        prompt = ANALYZE_DIFF_USER.format(
            base_num="1", base_title="Overview",
            compare_num="2", compare_title="Introduction",
            base_content="Base content",
            compare_content="Compare content",
            diff_summary="变更摘要",
        )
        assert "Base 章节" in prompt
        assert "Compare 章节" in prompt
        assert "变更摘要" in prompt

    def test_diff_user_prompt_lists_types(self):
        assert "feature-added" in ANALYZE_DIFF_USER
        assert "feature-changed" in ANALYZE_DIFF_USER
        assert "design-diff" in ANALYZE_DIFF_USER
        assert "param-diff" in ANALYZE_DIFF_USER

    def test_diff_user_prompt_asks_chinese_output(self):
        assert "中文" in ANALYZE_DIFF_USER
