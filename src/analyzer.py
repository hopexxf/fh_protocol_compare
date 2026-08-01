"""
LLM 差异分析模块

调用 LLM 对每对章节的 diff 结果进行语义分析：
  - 分类：feature-added / feature-changed / feature-removed / design-diff / param-diff / consistency-issue
  - 影响评估：高 / 中 / 低
  - 详细描述（中文）
  - 工作量提示

支持异步并发批量调用（concurrency=10），大幅加速。
"""

import asyncio
import json
import logging
import re
from typing import Optional

import httpx

from src.llm_client import get_llm_client, _load_gateway_token, _get_gateway_port

logger = logging.getLogger("fh_protocol_compare.analyzer")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _find_json_end(text: str) -> int:
    """
    从 text[0] 开始，找匹配最外层 } 的位置。
    text 必须以 '{' 开头。返回 -1 表示未找到。
    """
    if not text or text[0] != "{":
        return -1
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_json(raw: str) -> str:
    """
    从 LLM 输出中提取 JSON 字符串。
    优先匹配 ```json...``` 块；否则找最后一个可解析的 {...} 块。
    用平衡括号算法确保正确匹配嵌套结构。

    关键：找到的是最外层（包含内层所有子对象）的完整 JSON。
    对于 ````json ... ```` 块，直接从 ```json 后的第一个 { 开始，用平衡括号找最外层 }。
    对于纯 JSON fallback，从前向后扫描每个 {，取最大平衡位置（最外层）。
    """
    # 优先匹配 ```json ... ``` 块
    md = re.search(r'```json', raw)
    if md:
        after = raw[md.end():]
        brace = re.search(r'\{', after)
        if brace:
            start = md.end() + brace.start()
            json_text = raw[start:]
            end_pos = _find_json_end(json_text)
            if end_pos >= 0:
                return json_text[:end_pos + 1].strip()

    # fallback：从第一个 { 开始，找最大平衡位置（最外层对象）
    if raw.strip().startswith('{'):
        end_pos = _find_json_end(raw)
        if end_pos >= 0:
            return raw[:end_pos + 1].strip()

    # 最后兜底：从后向前找任何可解析的 {...}
    starts = [m.start() for m in re.finditer(r'\{', raw)]
    for start in reversed(starts):
        try:
            candidate = raw[start:]
            end_pos = _find_json_end(candidate)
            if end_pos < 0:
                continue
            try:
                json.loads(candidate[:end_pos + 1])
                return candidate[:end_pos + 1].strip()
            except json.JSONDecodeError:
                continue
        except Exception:
            continue
    return raw.strip()


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
{dynamic_hint}

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

ANALYZE_REMOVED_SYSTEM = """你是一位专业的 5G NR 前传（Front Haul）协议工程师，擅长分析 O-RAN 和 ASTRI 等不同标准组织的协议文档。

你的任务：判断一段仅存在于 Base 版本（Compare 版本无对应章节）的内容属于哪种情况，输出严格 JSON，不要输出 JSON 以外的任何内容。"""

ANALYZE_REMOVED_USER = """## 任务
以下章节仅存在于 Base 版本，Compare 版本中没有对应章节。请判断它属于哪种情况：

1. **feature-removed（功能删除）**：Compare 版本明确覆盖了同一技术领域，但该具体功能 / 字段 / 流程被移除或迁移。
2. **scope-diff（范围差异）**：Base（如 O-RAN）与 Compare（如 ASTRI）是不同标准组织，该主题本就不在 Compare 的范围内，并非被“删除”。**除非有强证据表明 Compare 在同等范围内遗漏了该能力，否则优先判为 scope-diff。**

## Base 独有章节（第 {base_num} 节 / {base_title}）

{base_content}

## 要求

1. 输出差异类型：feature-removed 或 scope-diff
2. 评估影响等级：
   - 高：接口变更、协议流程重构
   - 中：功能调整，部分模块需要修改
   - 低：文字 / 范围差异，不影响实现
3. 用**中文**撰写差异描述（2-5 句话）。
4. 提供工作量提示（1-2 句话）。

## 输出格式（严格 JSON）

{{
  "diffs": [
    {{
      "type": "feature-removed 或 scope-diff",
      "impact": "高/中/低",
      "base_quote": "Base 中的关键原文（英文）",
      "compare_quote": "",
      "description": "差异描述（中文）",
      "workload_hint": "工作量提示（中文）"
    }}
  ],
  "summary": "本节整体概述（中文，一句话）"
}}
"""

# ---------------------------------------------------------------------------
# 动态知识注入
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    """
    构建 SYSTEM prompt，注入核心业务知识（模块 1 + 模块 2）。
    
    从 config/knowledge.yml 加载 org_background 和 layer_responsibility。
    如文件不存在，降级为原始 ANALYZE_DIFF_SYSTEM。
    """
    from src.config_loader import get_knowledge
    
    knowledge = get_knowledge()
    
    if not knowledge:
        # 配置文件不存在，使用原始 prompt
        return ANALYZE_DIFF_SYSTEM
    
    parts = [
        "你是一位专业的 5G NR 前传（Front Haul）协议工程师，擅长分析 O-RAN 和 ASTRI 等不同标准组织的协议文档。",
        "",
        knowledge.get("org_background", ""),
        "",
        knowledge.get("layer_responsibility", ""),
        "",
        "你的任务：判断章节差异的类型与影响，输出严格 JSON，不要输出 JSON 以外的任何内容。",
    ]
    
    return "\n".join([p for p in parts if p])


def _build_removed_system_prompt() -> str:
    """
    构建 Base 独有章节的 SYSTEM prompt，仅注入模块 1（标准组织背景）。
    
    这是 scope-diff 判断的核心依据。
    """
    from src.config_loader import get_knowledge
    
    knowledge = get_knowledge()
    
    if not knowledge:
        return ANALYZE_REMOVED_SYSTEM
    
    parts = [
        "你是一位专业的 5G NR 前传协议工程师，擅长分析 O-RAN 和 ASTRI 等不同标准组织的协议文档。",
        "",
        knowledge.get("org_background", ""),
        "",
        "你的任务：判断章节差异的类型与影响，输出严格 JSON，不要输出 JSON 以外的任何内容。",
    ]
    
    return "\n".join([p for p in parts if p])


def _get_dynamic_hint(content: str, max_hints: int = 3) -> str:
    """
    根据章节内容匹配动态提示（模块 5），按优先级排序。
    
    Args:
        content: 章节 title + content 的组合文本
        max_hints: 最多返回几条提示
    
    Returns:
        动态提示字符串（如 "**动态提示**：\n- xxx\n- xxx"）或空字符串
    """
    from src.config_loader import get_knowledge
    
    knowledge = get_knowledge()
    patterns = knowledge.get("diff_patterns", {})
    
    if not patterns:
        return ""
    
    matched = []
    content_lower = content.lower()
    
    for pattern_name, pattern_data in patterns.items():
        keywords = pattern_data.get("keywords", [])
        # 任一关键词匹配即触发
        if any(kw.lower() in content_lower for kw in keywords):
            matched.append({
                "name": pattern_name,
                "hint": pattern_data.get("hint", ""),
                "priority": pattern_data.get("priority", 0),
            })
    
    if not matched:
        return ""
    
    # 按优先级降序排序，取前 N 个
    matched.sort(key=lambda x: x["priority"], reverse=True)
    top_matches = matched[:max_hints]
    
    hints = [f"- {m['hint']}" for m in top_matches]
    return "\n**动态提示**：\n" + "\n".join(hints)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _build_messages(diff_item: dict) -> list[dict]:
    """为单个 diff_item 构建 LLM messages"""
    base_id = diff_item.get("base_section_id")
    compare_id = diff_item.get("compare_section_id")

    if base_id and compare_id:
        # 对齐章节：注入动态提示
        combined_content = (
            diff_item.get("base_section_title", "") + " " +
            diff_item.get("base_content", "") + " " +
            diff_item.get("compare_section_title", "") + " " +
            diff_item.get("compare_content", "")
        )
        dynamic_hint = _get_dynamic_hint(combined_content)
        
        return [
            {"role": "system", "content": _build_system_prompt()},
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
                    dynamic_hint=dynamic_hint,
                ),
            },
        ]
    elif compare_id:
        return [
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
    else:
        # Base 独有章节：判为 feature-removed 还是 scope-diff（范围差异）
        # 注入标准组织背景知识（模块 1）
        return [
            {"role": "system", "content": _build_removed_system_prompt()},
            {
                "role": "user",
                "content": ANALYZE_REMOVED_USER.format(
                    base_num=diff_item.get("base_section_number", ""),
                    base_title=diff_item.get("base_section_title", ""),
                    base_content=diff_item.get("base_content", ""),
                ),
            },
        ]


def _parse_llm_result(diff_item: dict, raw: str) -> dict:
    """解析 LLM 输出并构造结果 dict"""
    base_id = diff_item.get("base_section_id")
    compare_id = diff_item.get("compare_section_id")

    if base_id and compare_id:
        result_key = "llm_result"
        expected_keys = ["diffs", "summary"]
    elif compare_id:
        result_key = "llm_added"
        expected_keys = ["type", "description"]
    else:
        result_key = "llm_result"
        expected_keys = ["diffs", "summary"]

    try:
        json_text = _extract_json(raw)
        parsed = json.loads(json_text)
        if all(k in parsed for k in expected_keys):
            return {**diff_item, result_key: parsed}
        else:
            logger.warning(f"[Analyzer] LLM 返回格式异常，keys={list(parsed.keys())}")
            return {**diff_item, result_key: {"diffs": [], "summary": raw[:200]}}
    except json.JSONDecodeError as e:
        logger.warning(f"[Analyzer] JSON 解析失败: {e}，raw={raw[:300]}")
        return {**diff_item, result_key: {"diffs": [], "summary": raw[:200]}}
    except Exception as e:
        logger.error(f"[Analyzer] 解析异常: {e}")
        return {**diff_item, result_key: {"diffs": [], "summary": f"解析异常: {e}"}}


# ---------------------------------------------------------------------------
# 同步单条分析
# ---------------------------------------------------------------------------

def analyze_diff_item(
    diff_item: dict,
    llm_client: Optional = None,
) -> dict:
    """分析单个 diff 项目的语义差异。"""
    client = llm_client or get_llm_client()
    client.set_session_label("fh_protocol_compare_single")
    base_id = diff_item.get("base_section_id")
    compare_id = diff_item.get("compare_section_id")

    messages = _build_messages(diff_item)
    result_key = "llm_result" if base_id else "llm_added"

    try:
        raw = client.chat(messages, temperature=0.1, max_tokens=1500)
        return _parse_llm_result(diff_item, raw)
    except Exception as e:
        logger.error(f"[Analyzer] 调用失败: {e}")
        if not compare_id:
            # 兜底：LLM 不可用时，Base 独有章节降级为 feature-removed
            return {
                **diff_item,
                "llm_result": {
                    "diffs": [{
                        "type": "feature-removed",
                        "impact": "中",
                        "base_quote": diff_item.get("base_content", "")[:200],
                        "compare_quote": "",
                        "description": "Base 版本中此章节在 Compare 版本中不存在（LLM 分析失败，默认标记）",
                        "workload_hint": "需确认该功能是否仍在 Compare 中实现",
                    }],
                    "summary": "Base 独有章节（LLM 兜底）",
                },
            }
        return {**diff_item, result_key: {"diffs": [], "summary": f"LLM 调用失败: {e}"}}


# ---------------------------------------------------------------------------
# 同步批量（fallback）
# ---------------------------------------------------------------------------

def _sync_batch(diff_results: list[dict], llm_client) -> list[dict]:
    client = llm_client or get_llm_client()
    client.set_session_label("fh_protocol_compare_batch")
    analyzed = []
    for i, item in enumerate(diff_results):
        if not item.get("has_diff") and item.get("base_section_id") and item.get("compare_section_id"):
            analyzed.append({**item, "llm_result": {"diffs": [], "summary": "无显著变更"}})
            continue
        logger.info(f"[Analyzer] 分析章节对 {i+1}/{len(diff_results)}: {item.get('base_section_title', '')} vs {item.get('compare_section_title', '')}")
        result = analyze_diff_item(item, llm_client=client)
        analyzed.append(result)
    try:
        client.cleanup_sessions()
    except Exception as e:
        logger.debug(f"[Analyzer] 会话清理异常: {e}")
    return analyzed


# ---------------------------------------------------------------------------
# 异步并发批量（主入口）
# ---------------------------------------------------------------------------

def _fmt_err(e: Exception) -> str:
    """构造异常描述。httpcore 1.0.9 的 ReadTimeout.str() 返回空串，
    必须同时记录异常类型名，否则失败原因会静默丢失（706 个失败无任何信息）。"""
    msg = str(e)
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def _is_mock_client(client) -> bool:
    """检测是否为 mock 对象（MagicMock 等），用于测试桩降级同步路径。

    真实 LLMClient 无 `called` 属性；MagicMock / 测试桩有。
    """
    return bool(client) and hasattr(client, "called")


async def analyze_diff_batch_async(
    diff_results: list[dict],
    llm_client: Optional = None,
    concurrency: int = 8,
) -> list[dict]:
    """
    异步并发批量分析 diff 结果。

    使用 httpx 异步流式请求 Gateway（并发度可调）。
    检测到 mock 对象时自动降级为同步。
    """
    client = llm_client or get_llm_client()
    client.set_session_label("fh_protocol_compare_batch")

    # 分类：需要分析的 vs 跳过
    todo = []
    skip = {}
    for i, item in enumerate(diff_results):
        if not item.get("has_diff") and item.get("base_section_id") and item.get("compare_section_id"):
            skip[i] = {**item, "llm_result": {"diffs": [], "summary": "无显著变更"}}
        else:
            todo.append((i, item))

    if not todo:
        return [skip.get(i, diff_results[i]) for i in range(len(diff_results))]

    # mock 对象（测试/调试桩）或无 token 时降级同步，直接调用 client.chat
    token = _load_gateway_token()
    if not token or _is_mock_client(llm_client):
        return _sync_batch(diff_results, llm_client)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    logger.debug("[Analyzer] Gateway token 已加载，端口将在每次请求时动态解析")

    total = len(todo)
    completed = 0
    lock = asyncio.Lock()
    MAX_RETRIES = 3

    async def fetch_one(sem: asyncio.Semaphore, idx: int, item: dict) -> tuple[int, dict]:
        nonlocal completed
        messages = _build_messages(item)
        payload = {
            "model": "openclaw",
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 1500,
            "stream": True,
            "user": "fh_protocol_compare_batch",
        }
        last_err: Optional[str] = None
        for attempt in range(MAX_RETRIES):
            # 每次重试重新解析 Gateway 端口：端口会随 Gateway 重启漂移
            gw_port = _get_gateway_port()
            url = f"http://localhost:{gw_port}/v1/chat/completions"
            try:
                async with sem:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client_http:
                        async with client_http.stream("POST", url, headers=headers, json=payload) as resp:
                            resp.raise_for_status()
                            content_parts = []
                            async for line in resp.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                data = line[5:].strip()
                                if data == "[DONE]":
                                    break
                                try:
                                    obj = json.loads(data)
                                    delta = obj.get("choices", [{}])[0].get("delta", {})
                                    if delta.get("content"):
                                        content_parts.append(delta["content"])
                                except (json.JSONDecodeError, IndexError, KeyError):
                                    continue
                raw = "".join(content_parts)
                result = _parse_llm_result(item, raw)
                return (idx, result)
            except Exception as e:
                last_err = _fmt_err(e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
        logger.warning(f"[Analyzer] 分析章节 {idx+1} 失败（重试 {MAX_RETRIES} 次）: {last_err}")
        result = {**item, "llm_result": {"diffs": [], "summary": f"LLM 调用失败: {last_err}"}}

        async with lock:
            completed += 1
            if completed % 10 == 0 or completed == total:
                logger.info(f"[Analyzer] 进度 {completed}/{total}")
        return (idx, result)

    sem = asyncio.Semaphore(concurrency)
    tasks = [fetch_one(sem, i, item) for i, item in todo]
    results_raw = await asyncio.gather(*tasks)

    result_map = dict(results_raw)
    result_map.update(skip)
    results = [result_map.get(i, diff_results[i]) for i in range(len(diff_results))]

    # 批量完成后清理 Gateway 会话
    try:
        client.cleanup_sessions()
    except Exception as e:
        logger.debug(f"[Analyzer] 会话清理异常: {e}")

    return results


def analyze_diff_batch(
    diff_results: list[dict],
    llm_client: Optional = None,
    concurrency: int = 8,
) -> list[dict]:
    """批量分析 diff 结果（默认异步并发，concurrency=8）。

    仅分析 has_diff=True 或独有章节的条目，跳过无变更的对齐章节。
    """
    return asyncio.run(analyze_diff_batch_async(diff_results, llm_client, concurrency))


# ---------------------------------------------------------------------------
# 统计汇总
# ---------------------------------------------------------------------------

def summarize_all(analyzed: list[dict]) -> dict:
    """汇总所有分析结果，生成统计信息。"""
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
