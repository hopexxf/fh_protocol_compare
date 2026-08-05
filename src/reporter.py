"""
Markdown 报告生成模块

将分析结果汇总生成最终的 Markdown 比对报告。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("fh_protocol_compare.reporter")

# ---------------------------------------------------------------------------
# 报告头
# ---------------------------------------------------------------------------

REPORT_TEMPLATE_HEADER = """# {base_name} vs {compare_name} — 协议差异比对报告

> 生成时间：{timestamp}
> Base 版本：{base_name}
> Compare 版本：{compare_name}
> 分析工具：FH Protocol Compare

---

## 📊 统计概览

| 指标 | 数值 |
|------|------|
| 比对章节数 | {total_sections} |
| 存在差异的章节 | {sections_with_diff} |
| 差异条目总数 | {total_diff_items} |

### 差异类型分布

| 类型 | 数量 |
|------|------|
{type_rows}

### 影响等级分布

| 等级 | 数量 | 说明 |
|------|------|------|
| 🟥 高 | {high} | 涉及接口变更、协议流程重构 |
| 🟨 中 | {medium} | 功能调整，部分模块需要修改 |
| 🟩 低 | {low} | 文字修正、格式调整，不影响实现 |

---

## 目录

{TOC}

---

"""

REPORT_TEMPLATE_DIFF_ITEM = """### {seq}. {title}

**类型**：{types}
**影响**：{impact}
**位置**：
  - Base：{base_loc}
  - Compare：{compare_loc}

**Base 原文**：
> {base_quote}

**Compare 原文**：
> {compare_quote}

**差异描述**：
{description}

**工作量提示**：
{workload_hint}

---
"""

REPORT_TEMPLATE_SECTION_HEADER = """## {seq}. {base_title} vs {compare_title}

_{section_summary}_

"""


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def _fmt_loc(doc_name: str, number: str, page: str) -> str:
    """构造带溯源的位置描述：文档名，第 X 节，P页码"""
    parts = [doc_name]
    if number:
        parts.append(f"第 {number} 节")
    if page:
        parts.append(f"P{page}")
    return "，".join(parts)


def generate_report(
    base_name: str,
    compare_name: str,
    analyzed: list[dict],
    stats: dict,
    output_path: Optional[str] = None,
) -> str:
    """
    生成完整的 Markdown 比对报告。

    Args:
        base_name: Base 文档名称（不含路径）
        compare_name: Compare 文档名称
        analyzed: analyzer.py 输出的分析结果列表
        stats: analyze.py 输出的统计信息
        output_path: 可选，报告输出路径

    Returns:
        报告 Markdown 文本
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S (GMT+8)")

    # 差异类型分布行
    type_rows = ""
    for t, cnt in sorted(stats.get("by_type", {}).items(), key=lambda x: -x[1]):
        label = {
            "feature-added": "功能新增 (feature-added)",
            "feature-changed": "功能变更 (feature-changed)",
            "feature-removed": "功能删除 (feature-removed)",
            "unknow-diff": "未知差异 (unknow-diff)",
            "design-diff": "设计差异 (design-diff)",
            "param-diff": "参数差异 (param-diff)",
            "consistency-issue": "一致性问题 (consistency-issue)",
            "new-feature": "新功能章节 (new-feature)",
            "new-design": "新设计章节 (new-design)",
            "new-parameter": "新参数章节 (new-parameter)",
            "scope-diff": "范围差异 (scope-diff)",
            "other": "其他 (other)",
        }.get(t, t)
        type_rows += f"| {label} | {cnt} |\n"

    # 目录
    toc_entries = []
    diff_seq = 0
    for item in analyzed:
        llm = item.get("llm_result", {})
        diffs = llm.get("diffs", [])
        if not diffs:
            continue
        diff_seq += 1
        base_t = item.get("base_section_title", item.get("compare_section_title", "未知"))
        toc_entries.append(f"{diff_seq}. [{base_t}](#{diff_seq}.-{base_t.replace(' ', '-').lower()})")
    toc = "\n".join(toc_entries) if toc_entries else "_（无显著差异）_"

    # 组装报告
    lines = []
    lines.append(REPORT_TEMPLATE_HEADER.format(
        base_name=base_name,
        compare_name=compare_name,
        timestamp=timestamp,
        total_sections=stats.get("total_sections", 0),
        sections_with_diff=stats.get("sections_with_diff", 0),
        total_diff_items=stats.get("total_diff_items", 0),
        type_rows=type_rows or "| — | — |\n",
        high=stats.get("by_impact", {}).get("高", 0),
        medium=stats.get("by_impact", {}).get("中", 0),
        low=stats.get("by_impact", {}).get("低", 0),
        TOC=toc,
    ))

    # 逐节内容
    diff_seq = 0
    for item in analyzed:
        llm = item.get("llm_result", {})
        diffs = llm.get("diffs", [])
        summary = llm.get("summary", "")

        if not diffs:
            continue

        diff_seq += 1
        base_t = item.get("base_section_title", "")
        compare_t = item.get("compare_section_title", "")

        # 节标题
        anchor = f"{diff_seq}.-{base_t.replace(' ', '-').lower()}" if base_t else str(diff_seq)
        lines.append(REPORT_TEMPLATE_SECTION_HEADER.format(
            seq=diff_seq,
            base_title=base_t or "—",
            compare_title=compare_t or "（Compare 独有）",
            section_summary=summary,
        ))

        # 差异条目
        item_types = []
        item_impacts = []
        for d in diffs:
            item_types.append(d.get("type", "unknown"))
            item_impacts.append(d.get("impact", "中"))

            # 位置描述（带原文溯源：文档名 / 章节号 / 页码）
            if item.get("base_section_id") is None:
                base_loc = "（Base 中不存在）"
            else:
                base_loc = _fmt_loc(
                    base_name,
                    item.get("base_section_number", ""),
                    item.get("base_page", ""),
                )
            if item.get("compare_section_id") is None:
                compare_loc = "（Compare 中不存在）"
            else:
                compare_loc = _fmt_loc(
                    compare_name,
                    item.get("compare_section_number", ""),
                    item.get("compare_page", ""),
                )

            # 原文引用（截断）
            base_quote = (d.get("base_quote") or "").replace("\n", " ")[:300]
            compare_quote = (d.get("compare_quote") or "").replace("\n", " ")[:300]

            if not base_quote:
                base_quote = "(无原文引用)"
            if not compare_quote:
                compare_quote = "(无原文引用)"

            lines.append(REPORT_TEMPLATE_DIFF_ITEM.format(
                seq=f"D-{diff_seq:03d}",
                title=f"{item.get('base_section_title', '')} - {d.get('type', '')}",
                types=d.get("type", "unknown"),
                impact=d.get("impact", "中"),
                base_loc=base_loc,
                compare_loc=compare_loc,
                base_quote=base_quote,
                compare_quote=compare_quote,
                description=d.get("description", "（LLM 未输出描述）"),
                workload_hint=d.get("workload_hint", "—"),
            ))

    report = "".join(lines)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info(f"[Reporter] 报告已写入: {output_path}")

    return report


def save_artifacts(
    output_dir: str,
    base_name: str,
    compare_name: str,
    base_md: str,
    compare_md: str,
    alignment: dict,
    diff_raw: list[dict],
    analyzed: list[dict],
    stats: dict,
    report_md: str,
    abstract_md: Optional[str] = None,
    full_diff_raw: Optional[list] = None,
) -> None:
    """
    将比对全流程产物归档到版本目录。

    目录结构：
      versions/{yyyymmdd}_{base}_vs_{compare}/
      ├── base_spec.md
      ├── compare_spec.md
      ├── alignment.json
      ├── diff_raw.json
      ├── analyzed.json
      ├── stats.json
      ├── report.md
      ├── report_abstract.md   （启用思路 3 摘要时）
      └── diff_raw_full.json  （子集模式下保存截断前完整差异列表）
    """
    from datetime import date

    date_str = date.today().strftime("%Y%m%d")
    safe_base = Path(base_name).stem.replace(" ", "_")
    safe_compare = Path(compare_name).stem.replace(" ", "_")
    version_dir = Path(output_dir) / f"{date_str}_{safe_base}_vs_{safe_compare}"
    version_dir.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "base_spec.md": base_md,
        "compare_spec.md": compare_md,
        "alignment.json": json.dumps(alignment, indent=2, ensure_ascii=False),
        "diff_raw.json": json.dumps(diff_raw, indent=2, ensure_ascii=False),
        "analyzed.json": json.dumps(analyzed, indent=2, ensure_ascii=False),
        "stats.json": json.dumps(stats, indent=2, ensure_ascii=False),
        "report.md": report_md,
    }
    if abstract_md is not None:
        artifacts["report_abstract.md"] = abstract_md
    if full_diff_raw is not None:
        artifacts["diff_raw_full.json"] = json.dumps(full_diff_raw, indent=2, ensure_ascii=False)

    for name, content in artifacts.items():
        path = version_dir / name
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug(f"[Reporter] 归档: {path}")

    logger.info(f"[Reporter] 产物已归档至: {version_dir}")


# ---------------------------------------------------------------------------
# 调试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    sample_analyzed = [{
        "base_section_title": "Overview",
        "compare_section_title": "Overview",
        "has_diff": True,
        "llm_result": {
            "diffs": [{
                "type": "feature-changed",
                "impact": "高",
                "base_quote": "Protocol version 1.0",
                "compare_quote": "Protocol version 2.0",
                "description": "协议版本从 1.0 升级到 2.0，接口消息结构有重大变更。",
                "workload_hint": "需重新设计接口适配层，影响多个模块。",
            }],
            "summary": "协议版本升级，相关接口有重大变更。",
        },
    }]
    sample_stats = {
        "total_sections": 1,
        "sections_with_diff": 1,
        "total_diff_items": 1,
        "by_type": {"feature-changed": 1},
        "by_impact": {"高": 1, "中": 0, "低": 0},
    }
    report = generate_report("spec_v1.pdf", "spec_v2.pdf", sample_analyzed, sample_stats)
    print(report)
