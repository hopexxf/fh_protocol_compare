"""
摘要生成模块（思路 3）

对分析报告（analyzed 结构化结果）浓缩后调用 LLM 生成精简概述
report_abstract.md，聚焦影响开发成本的关键差异。

- 与思路 2（聚类）解耦：可独立作用于普通 report。
- 浓缩时去除长原文引用（base_quote / compare_quote），只留
  type / impact / description / 位置。
- 复用 analyzer.call_gateway 走 Gateway 非流式路径（动态端口 / Token）。
- 默认 config abstract.enabled=False。
"""

import logging
from typing import Optional

logger = logging.getLogger("fh_protocol_compare.abstractor")

MAX_ABSTRACT_TOKENS = 4000

ABSTRACT_SYSTEM = """你是一位 5G NR 前传协议工程负责人，负责评估两份协议文档差异对开发成本的影响。
请基于给定的差异清单，输出一份**精简的执行摘要**，聚焦对开发工作量影响最大的关键点。
输出严格使用 Markdown，中文为主，关键术语保留英文。不要输出与清单无关的内容。"""

ABSTRACT_USER = """以下是协议差异清单（已去除原文引用，仅保留类型 / 影响 / 描述 / 位置）。
请生成一份精简的执行摘要，结构如下：

1. **执行摘要**：3-5 句话概括最核心的差异与开发影响。
2. **按维度分组的关键差异**：按 feature-added / changed / removed / design-diff / param-diff / consistency-issue 分组，每组只列**高 / 中影响**项。
3. **Top {top_n} 高影响变更**：按影响等级排序，列出最值得关注的 {top_n} 项（含位置）。
4. **工作量汇总**：按高 / 中 / 低影响汇总工作量提示。
5. **跨条目整合结论**：指出可合并处理、方向一致的差异集群。

## 差异清单
{condensed}
"""


def condense_analyzed(analyzed: list[dict]) -> str:
    """
    从 analyzed 结构化结果浓缩为紧凑文本（去除长原文引用）。

    返回 Markdown 文本，每个差异项一行（type / impact / description / 位置）。
    """
    lines = []
    seq = 0
    for item in analyzed:
        llm = item.get("llm_result", {}) or {}
        diffs = llm.get("diffs", []) or []
        summary = llm.get("summary", "")
        if not diffs:
            continue
        seq += 1
        base_t = item.get("base_section_title", "") or item.get("compare_section_title", "")
        comp_t = item.get("compare_section_title", "")
        base_num = item.get("base_section_number", "")
        comp_num = item.get("compare_section_number", "")
        base_page = item.get("base_page", "")
        comp_page = item.get("compare_page", "")
        loc_parts = []
        if base_t:
            loc_parts.append(f"Base 第{base_num}节 P{base_page}" if base_page else f"Base 第{base_num}节")
        if comp_t:
            loc_parts.append(f"Compare 第{comp_num}节 P{comp_page}" if comp_page else f"Compare 第{comp_num}节")
        loc = " / ".join(loc_parts) if loc_parts else "—"
        for d in diffs:
            lines.append(
                f"- [{seq}] {d.get('type', '')} | 影响:{d.get('impact', '')} | {base_t} | {loc}\n"
                f"    描述: {d.get('description', '')}"
            )
        if summary:
            lines.append(f"    (概述: {summary})")
    return "\n".join(lines)


def build_abstract_prompt(condensed: str, cfg: Optional[dict] = None) -> list[dict]:
    """构建摘要 LLM messages。"""
    top_n = (cfg or {}).get("top_n", 10)
    return [
        {"role": "system", "content": ABSTRACT_SYSTEM},
        {"role": "user", "content": ABSTRACT_USER.format(condensed=condensed, top_n=top_n)},
    ]


def generate_abstract(
    analyzed: list[dict],
    cfg: Optional[dict] = None,
    llm_call=None,
) -> str:
    """
    生成 report_abstract.md 正文。

    Args:
        analyzed: analyzer 输出的分析结果列表。
        cfg: 配置 dict（abstract 块）。
        llm_call: 可选注入的 LLM 调用函数 messages -> str（测试用 mock）；
                  为 None 时走真实 Gateway（analyzer.call_gateway）。

    Returns:
        Markdown 文本（report_abstract.md 内容）。
    """
    cfg = cfg or {}
    condensed = condense_analyzed(analyzed)
    if not condensed.strip():
        return "_（无显著差异，无需摘要）_"

    messages = build_abstract_prompt(condensed, cfg)

    if llm_call is None:
        from src.analyzer import call_gateway
        max_tokens = cfg.get("max_tokens", MAX_ABSTRACT_TOKENS)
        raw = call_gateway(messages, max_tokens=max_tokens)
    else:
        raw = llm_call(messages)

    return (raw or "").strip()
