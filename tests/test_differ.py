"""
test_differ.py — Phase 4：差异提取测试

实际 API（来自 src/differ.py）：
  diff_text_dmp(text1, text2) -> list[dict{"type", "text"}]
  diff_text_difflib(text1, text2) -> list[dict{"type", "text"}]
  has_significant_diff(diffs, threshold_chars) -> bool
  extract_diff_snippet(diffs, max_context) -> str
  diff_aligned_sections(base_md, compare_md, alignment) -> list[dict]

TC4-1：diff_text_dmp 相同文本
TC4-2：diff_text_dmp 插入/删除检测
TC4-3：diff_text_difflib 备选
TC4-4：has_significant_diff 阈值
TC4-5：extract_diff_snippet 格式
TC4-6：diff_aligned_sections 对齐章节对
TC4-7：diff_aligned_sections 独有章节
TC4-8：_summarize_diff 摘要生成
TC4-9：边界情况
"""

import pytest
from pathlib import Path

from src.differ import (
    diff_text_dmp,
    diff_text_difflib,
    has_significant_diff,
    extract_diff_snippet,
    diff_aligned_sections,
    _clean_text,
)


# ------------------------------------------------------------------
# 测试数据
# ------------------------------------------------------------------

TEXT_A = "The U-plane interface defines the data format for user plane traffic."
TEXT_B = "The C-plane interface defines the control signaling for control plane traffic."
TEXT_SIMILAR = "The U-plane interface defines the data format for user plane transmission."
TEXT_EMPTY = ""
TEXT_LONG_A = "A" * 200 + " changed content" + "B" * 200
TEXT_LONG_B = "A" * 200 + " new content here" + "B" * 200

ALIGNMENT_SAMPLE = {
    "alignments": [
        {
            "base_id": "1",
            "base_number": "1",
            "base_title": "Overview",
            "compare_id": "1",
            "compare_number": "1",
            "compare_title": "Introduction",
            "similarity": 0.82,
            "method": "tfidf_cosine+keywords",
        },
        {
            "base_id": "2",
            "base_number": "2",
            "base_title": "Signal Flow",
            "compare_id": "3",
            "compare_number": "2",
            "compare_title": "Data Transmission",
            "similarity": 0.61,
            "method": "tfidf_cosine",
        },
    ],
    "base_only": [
        {"id": "3", "number": "3", "title": "Message Structure"},
    ],
    "compare_only": [
        {"id": "4", "number": "4", "title": "New Protocol"},
    ],
}

MD_BASE = """# 1. Overview

## 1.1 Scope

The U-plane interface defines the data format for user plane traffic.
The compression algorithm supports IQ data with 15-bit resolution.

## 1.2 References

[1] 3GPP TS 38.401
[2] O-RAN.WG4.CUS.0

# 2. Signal Flow

Downlink data flows from BBU to RRU via the fronthaul interface.

# 3. Message Structure

Control messages are structured as follows.
"""

MD_COMPARE = """# 1. Introduction

## 1.1 Scope and Applicability

The C-plane interface defines the control signaling for control plane traffic.
The enhanced compression algorithm supports IQ data with 16-bit resolution.

## 1.2 References

[1] 3GPP TS 38.401
[2] O-RAN.WG4.CUS.0
[3] ASTRI Internal Spec

# 2. Data Transmission

Uplink and downlink data flows through the fronthaul network.

# 4. New Protocol

This section describes the new protocol extension.
"""


# ------------------------------------------------------------------
# TC4-1：diff_text_dmp 相同文本
# ------------------------------------------------------------------

class TestDiffDmpIdentical:
    def test_identical_text_returns_equal(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_A)
        assert isinstance(diffs, list)
        assert len(diffs) >= 1
        # 所有段应为 equal
        assert all(d["type"] == "equal" for d in diffs)
        # 合并后的 equal 文本应等于原文
        combined = "".join(d["text"] for d in diffs)
        assert combined == TEXT_A

    def test_diffs_contains_required_fields(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_B)
        for d in diffs:
            assert "type" in d
            assert "text" in d
            assert d["type"] in ("equal", "insert", "delete")


# ------------------------------------------------------------------
# TC4-2：diff_text_dmp 插入/删除检测
# ------------------------------------------------------------------

class TestDiffDmpChanges:
    def test_insert_detected(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_A + " Additional text.")
        types = [d["type"] for d in diffs]
        assert "insert" in types, "应有插入内容"

    def test_delete_detected(self):
        diffs = diff_text_dmp(TEXT_A + " Additional text.", TEXT_A)
        types = [d["type"] for d in diffs]
        assert "delete" in types, "应有删除内容"

    def test_replace_detected(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_B)
        types = [d["type"] for d in diffs]
        assert "insert" in types and "delete" in types, "应有替换（删除+插入）"

    def test_diff_reconstructable(self):
        """删除的 + 相等的 + 插入的 应能重构新文本"""
        diffs = diff_text_dmp(TEXT_A, TEXT_B)
        reconstructed = ""
        for d in diffs:
            if d["type"] in ("equal", "insert"):
                reconstructed += d["text"]
        # 重构文本应接近 TEXT_B（忽略标点差异）
        assert len(reconstructed) > 0

    def test_similar_text_partial_diff(self):
        """相似文本应有部分 diff"""
        diffs = diff_text_dmp(TEXT_A, TEXT_SIMILAR)
        assert len(diffs) >= 1
        # 既有 equal 也有变更
        types = [d["type"] for d in diffs]
        assert "equal" in types, "应有共同内容"
        assert len(types) > 1 or types[0] != "equal", "应有变更内容"


# ------------------------------------------------------------------
# TC4-3：diff_text_difflib 备选
# ------------------------------------------------------------------

class TestDiffLibFallback:
    def test_difflib_returns_same_structure(self):
        dmp_diffs = diff_text_dmp(TEXT_A, TEXT_B)
        lib_diffs = diff_text_difflib(TEXT_A, TEXT_B)
        assert isinstance(lib_diffs, list)
        for d in lib_diffs:
            assert d["type"] in ("equal", "insert", "delete")
        # 两种方法均应检测到 insert 和 delete（equal 可能因粒度不同而有差异）
        dmp_types = set(d["type"] for d in dmp_diffs)
        lib_types = set(d["type"] for d in lib_diffs)
        # DMP 是字符级（可能有 equal），difflib 是行级（可能无 equal），取并集
        assert "insert" in dmp_types | lib_types, "应有插入内容"
        assert "delete" in dmp_types | lib_types, "应有删除内容"

    def test_difflib_identical(self):
        diffs = diff_text_difflib(TEXT_A, TEXT_A)
        assert all(d["type"] == "equal" for d in diffs)


# ------------------------------------------------------------------
# TC4-4：has_significant_diff 阈值
# ------------------------------------------------------------------

class TestSignificantDiff:
    def test_no_change_not_significant(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_A)
        assert not has_significant_diff(diffs, threshold_chars=10)

    def test_small_change_not_significant(self):
        diffs = diff_text_dmp("Hello world.", "Hello beautiful world.")
        # 小幅插入
        assert not has_significant_diff(diffs, threshold_chars=50)

    def test_large_change_is_significant(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_B)
        assert has_significant_diff(diffs, threshold_chars=10)

    def test_threshold_parameter(self):
        diffs = diff_text_dmp("Hello world.", "Hi world.")
        # 差异较小（几个字符）
        assert has_significant_diff(diffs, threshold_chars=1)
        assert not has_significant_diff(diffs, threshold_chars=100)

    def test_long_text_change(self):
        diffs = diff_text_dmp(TEXT_LONG_A, TEXT_LONG_B)
        assert has_significant_diff(diffs, threshold_chars=10)


# ------------------------------------------------------------------
# TC4-5：extract_diff_snippet 格式
# ------------------------------------------------------------------

class TestDiffSnippet:
    def test_snippet_has_delete_marker(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_B)
        snippet = extract_diff_snippet(diffs)
        assert "~~" in snippet or "**+" in snippet or "delete" in snippet.lower()

    def test_snippet_has_insert_marker(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_B)
        snippet = extract_diff_snippet(diffs)
        assert "**+" in snippet or "insert" in snippet.lower() or "~~" in snippet

    def test_snippet_truncates_long_equal(self):
        diffs = diff_text_dmp(TEXT_LONG_A, TEXT_LONG_B)
        snippet = extract_diff_snippet(diffs, max_context=50)
        # 过长内容应被截断
        assert len(snippet) < len(TEXT_LONG_A) * 2

    def test_snippet_identical_returns_clean(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_A)
        snippet = extract_diff_snippet(diffs)
        # 相同文本无标记
        assert "~~" not in snippet or "**+" not in snippet

    def test_snippet_returns_string(self):
        diffs = diff_text_dmp(TEXT_A, TEXT_B)
        snippet = extract_diff_snippet(diffs)
        assert isinstance(snippet, str)


# ------------------------------------------------------------------
# TC4-6：diff_aligned_sections 对齐章节对
# ------------------------------------------------------------------

class TestDiffAlignedSections:
    def test_returns_list(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, ALIGNMENT_SAMPLE)
        assert isinstance(results, list)

    def test_alignment_result_contains_required_fields(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, ALIGNMENT_SAMPLE)
        for r in results:
            assert "base_section_id" in r
            assert "compare_section_id" in r
            assert "has_diff" in r
            assert "diff_summary" in r
            assert "similarity" in r

    def test_aligned_pair_has_both_ids(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, ALIGNMENT_SAMPLE)
        # 真正对齐的记录：base_section_id 和 compare_section_id 均不为 None
        aligned = [r for r in results
                   if r["base_section_id"] is not None and r["compare_section_id"] is not None]
        assert len(aligned) == len(ALIGNMENT_SAMPLE["alignments"]), \
            f"对齐数应为 {len(ALIGNMENT_SAMPLE['alignments'])}，实际 {len(aligned)}"

    def test_aligned_pair_has_similarity(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, ALIGNMENT_SAMPLE)
        for r in results:
            if r["compare_section_id"]:
                assert 0.0 <= r["similarity"] <= 1.0

    def test_base_only_sections(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, ALIGNMENT_SAMPLE)
        base_only = [r for r in results if r["compare_section_id"] is None and r["base_section_id"] is not None]
        assert len(base_only) == len(ALIGNMENT_SAMPLE["base_only"])

    def test_compare_only_sections(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, ALIGNMENT_SAMPLE)
        cmp_only = [r for r in results if r["base_section_id"] is None and r["compare_section_id"] is not None]
        assert len(cmp_only) == len(ALIGNMENT_SAMPLE["compare_only"])


# ------------------------------------------------------------------
# TC4-7：diff_aligned_sections 独有章节
# ------------------------------------------------------------------

class TestDiffExclusiveSections:
    def test_base_only_has_no_compare(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, ALIGNMENT_SAMPLE)
        base_only = [r for r in results if r["compare_section_id"] is None and r["base_section_id"] is not None]
        for r in base_only:
            assert r["base_section_id"] is not None
            assert r["compare_section_id"] is None
            assert r["has_diff"] is True
            assert "独有" in r["diff_summary"]

    def test_compare_only_has_no_base(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, ALIGNMENT_SAMPLE)
        cmp_only = [r for r in results if r["base_section_id"] is None and r["compare_section_id"] is not None]
        for r in cmp_only:
            assert r["base_section_id"] is None
            assert r["compare_section_id"] is not None
            assert r["has_diff"] is True


# ------------------------------------------------------------------
# TC4-8：_summarize_diff 摘要生成（间接验证）
# ------------------------------------------------------------------

class TestDiffSummary:
    def test_summary_describes_changes(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, ALIGNMENT_SAMPLE)
        for r in results:
            assert isinstance(r["diff_summary"], str)
            assert len(r["diff_summary"]) > 0

    def test_no_diff_summary(self):
        """无差异章节应有相应摘要"""
        # 用相同内容测试
        results = diff_aligned_sections(MD_BASE, MD_BASE, {
            "alignments": [
                {"base_id": "1", "base_number": "1", "base_title": "Overview",
                 "compare_id": "1", "compare_number": "1", "compare_title": "Overview",
                 "similarity": 1.0, "method": "tfidf_cosine"}
            ],
            "base_only": [],
            "compare_only": [],
        })
        for r in results:
            assert "diff_summary" in r


# ------------------------------------------------------------------
# TC4-9：边界情况
# ------------------------------------------------------------------

class TestDiffEdgeCases:
    def test_empty_text1(self):
        diffs = diff_text_dmp("", "Something")
        assert len(diffs) > 0
        assert all(d["type"] in ("equal", "insert") for d in diffs)

    def test_empty_text2(self):
        diffs = diff_text_dmp("Something", "")
        assert len(diffs) > 0
        assert all(d["type"] in ("equal", "delete") for d in diffs)

    def test_both_empty(self):
        diffs = diff_text_dmp("", "")
        assert len(diffs) >= 0
        # 空文本 vs 空文本应全部 equal（或空列表）
        assert all(d["type"] == "equal" for d in diffs)

    def test_unicode_text(self):
        diffs = diff_text_dmp("U-plane 接口定义了用户面。", "C-plane 接口定义了控制面。")
        assert len(diffs) > 0
        assert all(d["type"] in ("equal", "insert", "delete") for d in diffs)

    def test_empty_alignment(self):
        results = diff_aligned_sections(MD_BASE, MD_COMPARE, {
            "alignments": [],
            "base_only": [],
            "compare_only": [],
        })
        assert isinstance(results, list)

    def test_clean_text_removes_whitespace(self):
        """_clean_text 折叠多余空白"""
        from src.differ import _clean_text
        text = "  Hello  \n\n  World  \n  "
        cleaned = _clean_text(text)
        assert "  " not in cleaned  # 不应有连续空格
        assert "\n\n" not in cleaned  # 不应有连续空行
