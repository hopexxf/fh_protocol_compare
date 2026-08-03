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

ABSTRACT_USER = """## 任务

你是 5G NR 前传协议工程负责人。请基于差异清单，输出如下结构的执行摘要：

1. **工作量定位**：定性本轮比对差异的整体工作量级别，在以下三级中选一并说明理由：
   - 颠覆性重构（重新开发半个 L1 前传模块级别，差异无法通过配置或小补丁解决，涉及架构设计层面）
   - 局部改造（部分功能模块需要重写或新增，但整体架构可复用）
   - 配置适配（主要通过参数调整和接口适配解决，无需改动核心逻辑）

2. **关键差异详述（最多 3 项最高影响变更）**：每项包括维度标签、位置、核心差异描述、对开发的具体影响。三大维度参考框架（优先使用，如差异命中则详细写出）：
   - 维度 A：**逻辑平面架构** — O-RAN 典型四平面（C/U/S/M-plane）协议栈独立、数据流隔离；ASTRI/其他方案可能无独立 C/S/M 平面，C 面与 S 面被缝合进 U 面 eCPRI 载荷（消息拼接+私有定时包），M-Plane 相关章节被整体删除。开发影响：O-RAN 按 MsgType 分流的线程失效，须重写收包解析器处理"先解 C 面拼接信息、再解 U 面 IQ 数据"的串行逻辑。
   - 维度 B：**同步拓扑** — O-RAN 依赖 3GPP PTP（LLS-C1/C3、IEEE 1588 UDP 319/320、ITU-T G.8275.1）；其他方案可能不依赖 PTP 网络，采用私有 Timing packet（如 BBU 累计正确接收 N 个间隔约 1ms 的 Time 包后启动 PDSCH）。开发影响：实现基于计数器和私有包格式的启动状态机，不再依赖 SyncE，网络拓扑规划彻底变更。
   - 维度 C：**报文组包与资源映射** — O-RAN 标准以太帧通常 1 个 eCPRI 消息（MsgType 0/2）+ 4 字节应用层公共头（frameId/subframeId/slotId），支持 PRB 级切片（startPrbu/numPrbu），报文通常 <1500 字节；其他方案可能消息拼接（一帧强塞多个 eCPRI 消息），无标准 4 字节应用头，时隙信息塞进私有偏移位置，全带宽强制映射（N_RB），报文常达 3000~8000 字节。开发影响：数据指针偏移量计算全重构，标准解析宏不可用，DPDK mempool 须扩容支持巨帧否则丢包。

3. **其余差异简表**：将中 / 低影响的差异按"维度标签 | 类型 | 位置"三列合并为紧凑表格，每行一条。维度标签可自拟（如：压缩算法 / 同步方案 / 平面架构 / 接口格式 / 功能缺失 / 其他）。

4. **开发工作量汇总**：高影响 N 项 / 中影响 M 项 / 低影响 K 项，按上述工作量定位级别定性。

## 差异清单
{condensed}
"""


# 维度关键词（与 knowledge.yml diff_patterns 同步，按 priority 降序）
_DIMENSION_KEYWORDS = [
    (10, ["eCPRI", "前传", "fronthaul", "切分", "split"]),
    (10, ["Section Type", "C-plane", "control plane"]),
    (10, ["同步", "PTP", "1588", "SyncE", "时钟", "timing", "clock"]),
    (9,  ["压缩", "compression", "IQ", "quantization"]),
    (8,  ["FFT", "子载波", "scs", "Sub-carrier"]),
    (7,  ["波束", "beamforming", "Beam", "AxC", "eAxC"]),
    (6,  ["RU", "Radio Unit", "天线单元"]),
    (5,  ["QoS", "带宽", "bandwidth"]),
    (4,  ["M-plane", "管理", "management"]),
    (3,  ["UE", "user equipment", "终端"]),
    (2,  ["测试", "test specification"]),
    (1,  ["节能", "energy saving", "省电"]),
]


def _match_dimension(texts: list[str]) -> str:
    """从文本列表中匹配维度标签，返回第一个命中的维度名。"""
    combined = " ".join(t.lower() for t in texts if t)
    for priority, keywords in _DIMENSION_KEYWORDS:
        for kw in keywords:
            if kw.lower() in combined:
                # 返回维度描述（从 keywords 取第一个有中文的或第一个英文）
                return keywords[-1]  # 取最后一个（通常最具体）
    return "其他"


def condense_analyzed(analyzed: list[dict]) -> str:
    """
    从 analyzed 结构化结果浓缩为紧凑文本，为 LLM 提供充足上下文：
    - 维度标签（知识匹配）
    - 类型 / 影响 / 位置
    - 关键原文片段（base_quote / compare_quote 各截 150 字符）
    - 差异描述摘要
    """
    lines = []
    for seq, item in enumerate(analyzed, 1):
        llm = item.get("llm_result", {}) or {}
        diffs = llm.get("diffs", []) or []
        if not diffs:
            continue

        # 位置
        base_t = item.get("base_section_title", "")
        comp_t = item.get("compare_section_title", "")
        base_num = item.get("base_section_number", "")
        comp_num = item.get("compare_section_number", "")
        base_page = item.get("base_page", "")
        comp_page = item.get("compare_page", "")
        loc_parts = []
        if base_t:
            loc_parts.append(f"Base {base_num or base_t} P{base_page}" if base_page else f"Base {base_num or base_t}")
        if comp_t:
            loc_parts.append(f"Compare {comp_num or comp_t} P{comp_page}" if comp_page else f"Compare {comp_num or comp_t}")
        loc = " / ".join(loc_parts) if loc_parts else "—"

        # 维度标签
        dimension = _match_dimension([
            base_t, comp_t,
            item.get("base_content", "")[:300],
            item.get("compare_content", "")[:300],
            *[d.get("description", "") for d in diffs],
        ])

        for d in diffs:
            impact = d.get("impact", "").lower()
            dim = dimension
            typ = d.get("type", "")
            desc = d.get("description", "")

            # 原文片段（各截 150）
            bq = (item.get("base_content", "") or "")[:150]
            cq = (item.get("compare_content", "") or "")[:150]
            if bq and bq != item.get("base_content", "")[:150]:
                bq = bq.rstrip() + " ..."
            if cq and cq != item.get("compare_content", "")[:150]:
                cq = cq.rstrip() + " ..."

            lines.append(
                f"### [{seq}] {dim} | {typ} | 影响:{impact} | {loc}\n"
                f"差异描述: {desc}\n"
                f"Base 原文: {bq or '(无)'}\n"
                f"Compare 原文: {cq or '(无)'}"
            )
    return "\n\n".join(lines)


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
