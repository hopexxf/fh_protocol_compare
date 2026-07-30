"""
LLM 差异分析模块

调用 LLM 对每对章节的 diff 结果进行语义分析：
  - 分类：feature-added / feature-changed / feature-removed / design-diff / param-diff / consistency-issue
  - 影响评估：高 / 中 / 低
  - 详细描述（中文）
  - 工作量提示
"""

import json
import logging
from typing import Optional

from src.llm_client import get_llm_client

logger = logging.getLogger("fh_protocol_compare.analyzer")

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

ANALYZE_DIFF_SYSTEM = """你是一位专业的 5G NR 前传（Front Haul）协议工程师，擅长分析 O-RAN 和 ASTRI 等不同标准组织的协议文档。

你的任务是对比 Base 版本和 Compare 版本的协议文档章节，分析其中的差异，重点关注：
1. **U-plane 字段差异**（U-Plane, User Plane, IQ Data, 字段定义）
2. **C-plane 信道传输**（C-Plane, Control Plane, 信道映射）
3. **消息结构与 Information Element**
4. **定时与同步机制**
5. **功能/设计变更**
6. **一致性错误**

输出严格遵循 JSON 格式，不要输出 JSON 以外的任何内容。"""

ANALYZE_DIFF_USER = """## 任务
对比以下两个协议文档章节的差异，从**功能**、**设计**、**参数**三个维度进行分析。

## Base 章节（第 {base_num} 节 / {base_title}）

{base_content}

## Compare 章节（第 {compare_num} 节 / {compare_title}）

{compare_content}

## 原始 Diff 摘要

{diff_summary}

## 要求

1. 判断差异类型（可多选）：
   - `feature-added`：Compare 中新增的功能
   - `feature-changed`：两者均有但描述/范围变化
   - `feature-removed`：Base 中有、Compare 中移除的功能
   - `design-diff`：消息结构、状态机、流程步骤变化
   - `param-diff`：字段定义、阈值、常量值变化
   - `consistency-issue`：文档内部描述不自洽

2. 评估影响等级：
   - 高：涉及接口变更、协议流程重构，需要大量开发工作
   - 中：功能调整，部分模块需要修改
   - 低：文字修正、格式调整，不影响实现

3. 用**中文**撰写差异描述（2-5句话），说明变化的具体内容。

4. 提供工作量提示（1-2句话），说明开发实现时的注意点。

## 输出格式（严格 JSON）

{{
  "diffs": [
    {{
      "type": "feature-changed",
      "impact": "高",
      "base_quote": "Base 中的关键原文（英文）",
      "compare_quote": "Compare 中的关键原文（英文）",
      "description": "差异描述（中文）",
      "workload_hint": "工作量提示（中文）"
    }}
  ],
  "summary": "本节整体概述（中文，一句话）"
}}

如果两节内容完全一致或无显著差异，返回：
{{
  "diffs": [],
  "summary": "无显著差异"
}}
"""

ANALYZE_ADDED_SYSTEM = """你是一位专业的 5G NR 前传协议工程师。"""

ANALYZE_ADDED_USER = """## 任务
分析以下 Compare 版本文档中的**独有章节**（Base 中不存在），识别其内容类型。

## Compare 独有章节（第 {compare_num} 节 / {compare_title}）

{compare_content}

## 要求

判断这个章节的内容类型：
- `new-feature`：全新的功能描述
- `new-design`：新的设计/架构描述
- `new-parameter`：新的参数/常量定义
- `other`：其他类型

用中文撰写简要描述（1-3句话）。

## 输出格式（严格 JSON）

{{
  "type": "new-feature",
  "description": "描述（中文）"
}}
"""

# ---------------------------------------------------------------------------
# 分析函数
# ---------------------------------------------------------------------------

def analyze_diff_item(
    diff_item: dict,
    llm_client: Optional = None,
) -> dict:
    """
    分析单个 diff 项目的语义差异。

    Args:
        diff_item: differ.py 输出的单个 diff 条目
        llm_client: LLM 客户端（可选，默认全局）

    Returns:
        dict（含 LLM 分析结果）：
        {
            "section_pair": ...,
            "llm_result": {
                "diffs": [...],
                "summary": str,
            },
        }
    """
    client = llm_client or get_llm_client()

    base_id = diff_item.get("base_section_id")
    compare_id = diff_item.get("compare_section_id")

    # 两种情况：1. 对齐章节对  2. 独有章节
    if base_id and compare_id:
        messages = [
            {"role": "system", "content": ANALYZE_DIFF_SYSTEM},
            {
                "role": "user",
                "content": ANALYZE_DIFF_USER.format(
                    base_num=diff_item.get("base_section_number", ""),
                    base_title=diff_item.get("base_section_title", ""),
                    compare_num=diff_item.get("compare_section_number", ""),
                    compare_title=diff_item.get("compare_section_title", ""),
                    base_content=diff_item.get("base_content", "（无内容）"),
                    compare_content=diff_item.get("compare_content", "（无内容）"),
                    diff_summary=diff_item.get("diff_summary", ""),
                ),
            },
        ]
        result_key = "llm_result"
        expected_keys = ["diffs", "summary"]
    elif compare_id:
        messages = [
            {"role": "system", "content": ANALYZE_ADDED_SYSTEM},
            {
                "role": "user",
                "content": ANALYZE_ADDED_USER.format(
                    compare_num=diff_item.get("compare_section_number", ""),
                    compare_title=diff_item.get("compare_section_title", ""),
                    compare_content=diff_item.get("compare_content", ""),
                ),
            },
        ]
        result_key = "llm_added"
        expected_keys = ["type", "description"]
    else:
        # Base 独有章节，暂不深入分析
        return {
            **diff_item,
            "llm_result": {
                "diffs": [{
                    "type": "feature-removed",
                    "impact": "中",
                    "base_quote": diff_item.get("base_content", "")[:200],
                    "compare_quote": "",
                    "description": "Base 版本中此章节在 Compare 版本中已被移除",
                    "workload_hint": "需确认该功能是否仍在 Compare 中实现",
                }],
                "summary": "Base 独有章节",
            },
        }

    try:
        raw = client.chat(messages, temperature=0.1, max_tokens=1500)
        parsed = json.loads(raw)
        # 简单校验
        if all(k in parsed for k in expected_keys):
            return {**diff_item, result_key: parsed}
        else:
            logger.warning(f"[Analyzer] LLM 返回格式异常，keys={list(parsed.keys())}")
            return {**diff_item, result_key: {"diffs": [], "summary": raw[:200]}}
    except json.JSONDecodeError as e:
        logger.warning(f"[Analyzer] JSON 解析失败: {e}，raw={raw[:200] if 'raw' in dir() else 'N/A'}")
        return {**diff_item, result_key: {"diffs": [], "summary": raw[:200] if 'raw' in dir() else str(e)}}
    except Exception as e:
        logger.error(f"[Analyzer] 调用失败: {e}")
        return {**diff_item, result_key: {"diffs": [], "summary": f"LLM 调用失败: {e}"}}


def analyze_diff_batch(
    diff_results: list[dict],
    llm_client: Optional = None,
) -> list[dict]:
    """
    批量分析 diff 结果。

    仅分析 has_diff=True 或独有章节的条目，跳过无变更的对齐章节。
    """
    client = llm_client or get_llm_client()
    analyzed = []

    for i, item in enumerate(diff_results):
        if not item.get("has_diff") and item.get("base_section_id") and item.get("compare_section_id"):
            # 无显著变更，跳过 LLM 分析
            analyzed.append({**item, "llm_result": {"diffs": [], "summary": "无显著变更"}})
            continue

        logger.info(f"[Analyzer] 分析章节对 {i+1}/{len(diff_results)}: {item.get('base_section_title', '')} vs {item.get('compare_section_title', '')}")
        result = analyze_diff_item(item, llm_client=client)
        analyzed.append(result)

    return analyzed


# ---------------------------------------------------------------------------
# 统计汇总
# ---------------------------------------------------------------------------

def summarize_all(analyzed: list[dict]) -> dict:
    """
    汇总所有分析结果，生成统计信息。
    """
    stats = {
        "total_sections": len(analyzed),
        "sections_with_diff": 0,
        "by_type": {},
        "by_impact": {"高": 0, "中": 0, "低": 0},
    }

    all_diffs = []
    for item in analyzed:
        llm = item.get("llm_result", {})
        diffs = llm.get("diffs", [])
        if diffs:
            stats["sections_with_diff"] += 1
            for d in diffs:
                all_diffs.append(d)
                t = d.get("type", "unknown")
                stats["by_type"][t] = stats["by_type"].get(t, 0) + 1
                impact = d.get("impact", "中")
                stats["by_impact"][impact] = stats["by_impact"].get(impact, 0) + 1

    stats["total_diff_items"] = len(all_diffs)
    return stats
