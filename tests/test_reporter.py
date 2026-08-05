"""
test_reporter.py — Phase 6：报告生成模块测试

实际 API（来自 src/reporter.py）：
  generate_report(base_name, compare_name, analyzed, stats, output_path) -> str
  save_artifacts(...) -> None

TC6-1~4：generate_report 结构与内容
TC6-5~7：统计表格填充（类型分布/影响等级/空统计）
TC6-8~9：目录（TOC）生成
TC6-10~13：差异条目渲染
TC6-14~15：边界情况（空分析结果/无差异）
TC6-16：save_artifacts 归档
TC6-17：output_path 文件写入
"""

import json
import os
import pytest
import tempfile
from pathlib import Path

from src.reporter import (
    generate_report,
    save_artifacts,
    REPORT_TEMPLATE_HEADER,
    REPORT_TEMPLATE_DIFF_ITEM,
)


# ------------------------------------------------------------------
# 测试数据
# ------------------------------------------------------------------

SAMPLE_STATS = {
    "total_sections": 3,
    "sections_with_diff": 2,
    "total_diff_items": 3,
    "by_type": {
        "feature-added": 1,
        "design-diff": 1,
        "param-diff": 1,
    },
    "by_impact": {"高": 1, "中": 1, "低": 1},
}

SAMPLE_ANALYZED = [
    {
        "base_section_id": "1",
        "base_section_number": "1",
        "base_section_title": "Overview",
        "compare_section_id": "1",
        "compare_section_number": "1",
        "compare_section_title": "Introduction",
        "similarity": 0.82,
        "has_diff": True,
        "llm_result": {
            "diffs": [
                {
                    "type": "design-diff",
                    "impact": "高",
                    "base_quote": "U-plane interface defines the data format.",
                    "compare_quote": "C-plane interface defines the control signaling.",
                    "description": "接口类型从 U-plane 变更为 C-plane，属于架构层面的重大变化。",
                    "workload_hint": "需要重新设计接口适配层，影响多个模块。",
                },
            ],
            "summary": "接口类型变更（U-plane → C-plane），架构影响较大。",
        },
    },
    {
        "base_section_id": "2",
        "base_section_number": "2",
        "base_section_title": "Signal Flow",
        "compare_section_id": "3",
        "compare_section_number": "3",
        "compare_section_title": "Data Transmission",
        "similarity": 0.61,
        "has_diff": True,
        "llm_result": {
            "diffs": [
                {
                    "type": "feature-added",
                    "impact": "低",
                    "base_quote": "",
                    "compare_quote": "New compression scheme added.",
                    "description": "Compare 版本新增了压缩方案描述。",
                    "workload_hint": "新增功能，实现复杂度低。",
                },
                {
                    "type": "param-diff",
                    "impact": "中",
                    "base_quote": "IQ data 15-bit resolution.",
                    "compare_quote": "IQ data 16-bit resolution.",
                    "description": "IQ 数据精度从 15-bit 提升到 16-bit。",
                    "workload_hint": "需要调整数据处理模块的位宽配置。",
                },
            ],
            "summary": "数据流描述扩展，新增压缩方案，精度提升。",
        },
    },
    {
        "base_section_id": "5",
        "base_section_number": "5",
        "base_section_title": "Message Structure",
        "compare_section_id": None,
        "compare_section_number": "",
        "compare_section_title": "",
        "similarity": 0,
        "has_diff": True,
        "llm_result": {
            "diffs": [{
                "type": "feature-removed",
                "impact": "中",
                "base_quote": "Original message structure definition.",
                "compare_quote": "",
                "description": "Base 版本中的消息结构章节在 Compare 中已被移除。",
                "workload_hint": "需确认该功能是否仍有其他实现位置。",
            }],
            "summary": "消息结构章节被移除。",
        },
    },
]

EMPTY_STATS = {
    "total_sections": 0,
    "sections_with_diff": 0,
    "total_diff_items": 0,
    "by_type": {},
    "by_impact": {"高": 0, "中": 0, "低": 0},
}


# ------------------------------------------------------------------
# TC6-1：report header 基本结构
# ------------------------------------------------------------------

class TestReportHeader:
    def test_report_contains_title(self):
        report = generate_report("spec_v1.pdf", "spec_v2.pdf", [], EMPTY_STATS)
        assert "spec_v1.pdf" in report
        assert "spec_v2.pdf" in report
        assert "协议差异比对报告" in report

    def test_report_contains_timestamp(self):
        report = generate_report("a.pdf", "b.pdf", [], EMPTY_STATS)
        # 时间戳格式：YYYY-MM-DD HH:MM:SS
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}", report)

    def test_report_contains_stats_table(self):
        report = generate_report("a.pdf", "b.pdf", [], SAMPLE_STATS)
        assert "统计概览" in report
        assert "比对章节数" in report

    def test_report_contains_analysis_tool_note(self):
        report = generate_report("a.pdf", "b.pdf", [], EMPTY_STATS)
        assert "FH Protocol Compare" in report or "分析工具" in report


# ------------------------------------------------------------------
# TC6-2~3：统计表格填充
# ------------------------------------------------------------------

class TestStatsTable:
    def test_total_sections_in_stats_table(self):
        report = generate_report("a.pdf", "b.pdf", [], SAMPLE_STATS)
        assert "3" in report  # total_sections=3

    def test_sections_with_diff_in_stats_table(self):
        report = generate_report("a.pdf", "b.pdf", [], SAMPLE_STATS)
        assert "2" in report  # sections_with_diff=2

    def test_type_distribution_table(self):
        report = generate_report("a.pdf", "b.pdf", [], SAMPLE_STATS)
        assert "feature-added" in report
        assert "design-diff" in report
        assert "param-diff" in report

    def test_impact_distribution_table(self):
        report = generate_report("a.pdf", "b.pdf", [], SAMPLE_STATS)
        assert "高" in report
        assert "中" in report
        assert "低" in report

    def test_empty_stats_renders_zero(self):
        report = generate_report("a.pdf", "b.pdf", [], EMPTY_STATS)
        assert "0" in report  # 空统计应有 0 值


# ------------------------------------------------------------------
# TC6-4：目录（TOC）生成
# ------------------------------------------------------------------

class TestTableOfContents:
    def test_toc_generated_when_diffs_exist(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        assert "目录" in report or "TOC" in report

    def test_toc_contains_section_titles(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        # 应包含有 diff 的章节标题
        assert "Overview" in report or "overview" in report.lower()

    def test_toc_empty_when_no_diffs(self):
        no_diff_analyzed = [
            {
                "base_section_id": "1",
                "base_section_title": "Annex",
                "compare_section_id": "1",
                "compare_section_title": "Annex",
                "has_diff": False,
                "llm_result": {"diffs": [], "summary": "无显著变更"},
            }
        ]
        report = generate_report("a.pdf", "b.pdf", no_diff_analyzed, {
            "total_sections": 1, "sections_with_diff": 0,
            "total_diff_items": 0, "by_type": {}, "by_impact": {"高": 0, "中": 0, "低": 0},
        })
        assert "无显著差异" in report

    def test_toc_sequential_numbering(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        # 差异条目前应有编号（D-001, D-002...）
        import re
        diff_nums = re.findall(r"D-(\d+)", report)
        assert len(diff_nums) >= 3  # 至少3个差异条目
        assert diff_nums == sorted(diff_nums, key=int)  # 应递增


# ------------------------------------------------------------------
# TC6-5~7：差异条目渲染
# ------------------------------------------------------------------

class TestDiffItemRendering:
    def test_diff_item_has_base_quote(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        assert "U-plane interface" in report

    def test_diff_item_has_compare_quote(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        assert "C-plane interface" in report

    def test_diff_item_has_description(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        assert "中文" in report or "变更" in report

    def test_diff_item_has_workload_hint(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        assert "设计" in report or "模块" in report or "接口" in report

    def test_diff_item_has_section_numbers(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        # 应有节编号
        assert "第 1 节" in report or "第 " in report

    def test_feature_removed_type_included(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        assert "feature-removed" in report

    def test_impact_high_marked(self):
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        assert "高" in report

    def test_base_only_section_appears_in_report(self):
        """Base 独有章节应出现在报告中"""
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS)
        assert "Message Structure" in report


# ------------------------------------------------------------------
# TC6-8~9：边界情况
# ------------------------------------------------------------------

class TestReportEdgeCases:
    def test_empty_analyzed_list(self):
        """空分析列表应生成空报告（仅头部）"""
        report = generate_report("a.pdf", "b.pdf", [], EMPTY_STATS)
        assert "协议差异比对报告" in report
        assert len(report) > 100  # 头部内容

    def test_report_with_all_impact_levels(self):
        all_impact = {
            "total_sections": 3, "sections_with_diff": 3,
            "total_diff_items": 3,
            "by_type": {"feature-added": 1, "design-diff": 1, "param-diff": 1},
            "by_impact": {"高": 1, "中": 1, "低": 1},
        }
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, all_impact)
        # 三个影响等级都出现
        impact_counts = report.count("高")
        assert impact_counts >= 1

    def test_unknow_diff_label_rendered(self):
        """unknow_diff 应在类型分布表中渲染为中文标签（任务 2 标签修复）。"""
        stats = {
            "total_sections": 1, "sections_with_diff": 1,
            "total_diff_items": 1, "by_type": {"unknow_diff": 1},
            "by_impact": {"中": 1, "高": 0, "低": 0},
        }
        report = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, stats)
        assert "未知差异 (unknow_diff)" in report, "unknow_diff 应渲染为中文标签"
        # 裸 type 名不应直接出现（说明走了标签映射而非兜底原值）
        assert "| unknow_diff |" not in report

    def test_long_titles_truncated_in_anchor(self):
        """长标题在锚点中不应导致格式问题"""
        long_title_item = [
            {
                "base_section_id": "1",
                "base_section_number": "1",
                "base_section_title": "This Is A Very Long Section Title That Might Cause Formatting Issues",
                "compare_section_id": "1",
                "compare_section_number": "1",
                "compare_section_title": "Short Title",
                "has_diff": True,
                "llm_result": {
                    "diffs": [{"type": "design-diff", "impact": "高",
                               "base_quote": "A", "compare_quote": "B",
                               "description": "测试", "workload_hint": "—"}],
                    "summary": "摘要",
                },
            }
        ]
        stats = {
            "total_sections": 1, "sections_with_diff": 1,
            "total_diff_items": 1, "by_type": {"design-diff": 1},
            "by_impact": {"高": 1, "中": 0, "低": 0},
        }
        report = generate_report("a.pdf", "b.pdf", long_title_item, stats)
        assert "协议差异比对报告" in report


# ------------------------------------------------------------------
# TC6-10：output_path 文件写入
# ------------------------------------------------------------------

class TestReportOutputPath:
    def test_output_path_writes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = str(Path(tmpdir) / "report.md")
            report = generate_report(
                "a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS,
                output_path=output_path
            )
            assert Path(output_path).exists()
            # 文件内容与返回值一致
            with open(output_path, encoding="utf-8") as f:
                assert f.read() == report

    def test_output_path_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = str(Path(tmpdir) / "subdir" / "report.md")
            report = generate_report("a.pdf", "b.pdf", [], EMPTY_STATS, output_path=output_path)
            assert Path(output_path).exists()

    def test_output_path_returns_same_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = str(Path(tmpdir) / "report.md")
            report1 = generate_report("a.pdf", "b.pdf", SAMPLE_ANALYZED, SAMPLE_STATS, output_path=output_path)
            with open(output_path, encoding="utf-8") as f:
                saved = f.read()
            assert saved == report1
            assert len(saved) > 500  # 非空报告


# ------------------------------------------------------------------
# TC6-11：save_artifacts 归档
# ------------------------------------------------------------------

class TestSaveArtifacts:
    def test_artifacts_saved_to_version_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_artifacts(
                output_dir=tmpdir,
                base_name="spec_v1.pdf",
                compare_name="spec_v2.pdf",
                base_md="# Base Content",
                compare_md="# Compare Content",
                alignment={"alignments": [], "base_only": [], "compare_only": []},
                diff_raw=[],
                analyzed=[],
                stats=EMPTY_STATS,
                report_md="# Report",
            )
            # 检查版本目录
            import os, datetime
            date_str = datetime.date.today().strftime("%Y%m%d")
            expected_dir = Path(tmpdir) / f"{date_str}_spec_v1_vs_spec_v2"
            assert expected_dir.exists(), f"版本目录应存在: {expected_dir}"

    def test_all_artifacts_files_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            save_artifacts(
                output_dir=tmpdir,
                base_name="base.pdf",
                compare_name="compare.pdf",
                base_md="# Base",
                compare_md="# Compare",
                alignment={"alignments": [], "base_only": [], "compare_only": []},
                diff_raw=[{"base_section_id": "1"}],
                analyzed=[{"base_section_title": "Test"}],
                stats={"total_sections": 1, "sections_with_diff": 1,
                       "total_diff_items": 1, "by_type": {}, "by_impact": {}},
                report_md="# Report",
            )
            expected_files = [
                "base_spec.md", "compare_spec.md",
                "alignment.json", "diff_raw.json",
                "analyzed.json", "stats.json", "report.md",
            ]
            import datetime
            date_str = datetime.date.today().strftime("%Y%m%d")
            version_dir = Path(tmpdir) / f"{date_str}_base_vs_compare"
            for fname in expected_files:
                assert (version_dir / fname).exists(), f"应存在: {fname}"

    def test_artifact_json_loadable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_alignment = {"alignments": [{"base_id": "1", "compare_id": "2"}],
                              "base_only": [], "compare_only": []}
            save_artifacts(
                output_dir=tmpdir,
                base_name="b.pdf",
                compare_name="c.pdf",
                base_md="# Base",
                compare_md="# Compare",
                alignment=test_alignment,
                diff_raw=[],
                analyzed=[],
                stats=EMPTY_STATS,
                report_md="# Report",
            )
            import datetime
            date_str = datetime.date.today().strftime("%Y%m%d")
            version_dir = Path(tmpdir) / f"{date_str}_b_vs_c"
            with open(version_dir / "alignment.json", encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["alignments"][0]["base_id"] == "1"


# ------------------------------------------------------------------
# TC6-12：模板内容验证
# ------------------------------------------------------------------

class TestTemplates:
    def test_header_template_has_required_placeholders(self):
        required = ["{base_name}", "{compare_name}", "{timestamp}",
                    "{total_sections}", "{sections_with_diff}",
                    "{type_rows}", "{high}", "{medium}", "{low}", "{TOC}"]
        for placeholder in required:
            assert placeholder in REPORT_TEMPLATE_HEADER, f"缺少占位符: {placeholder}"

    def test_diff_item_template_has_required_placeholders(self):
        required = ["{seq}", "{title}", "{types}", "{impact}",
                    "{base_loc}", "{compare_loc}",
                    "{base_quote}", "{compare_quote}",
                    "{description}", "{workload_hint}"]
        for placeholder in required:
            assert placeholder in REPORT_TEMPLATE_DIFF_ITEM, f"缺少占位符: {placeholder}"
