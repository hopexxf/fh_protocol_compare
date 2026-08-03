"""
差异提取模块

基于对齐映射，对每对章节执行文本级 diff，输出包含原始文本定位的 diff_raw.md。

支持策略：
  1. diff-match-patch（Google）：字符/词级 diff，适合精确变更定位
  2. difflib（Python 标准库）：行级 diff，兜底
"""

import logging
import re
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("fh_protocol_compare.differ")

# ---------------------------------------------------------------------------
# vendor diff_match_patch
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _extract_table_page_hint(text: str) -> str:
    """
    从 Markdown 内容中提取表格页码标记。
    支持格式：<!-- TABLE: page=N index=M --> 或 <!-- table_page=N -->
    返回第一个匹配到的页码字符串，未找到返回空字符串。
    """
    if not text:
        return ""
    m = re.search(r"<!--\s*(?:TABLE:\s*page|table_page)=(\d+)", text)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# vendor diff_match_patch
# ---------------------------------------------------------------------------

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
sys.path.insert(0, str(_VENDOR_DIR))
try:
    from diff_match_patch import diff_match_patch
    _DMP = diff_match_patch()
except ImportError:
    _DMP = None
    logger.warning("[Diff] diff_match_patch 加载失败，将使用 difflib")


# ---------------------------------------------------------------------------
# 文本清洗
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """清洗文本：去除多余空白、标准化"""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        # 折叠连续空白
        line = re.sub(r"[ \t]+", " ", line)
        if line.strip():
            cleaned.append(line.strip())
    return "\n".join(cleaned)


def _split_into_sentences(text: str) -> list[str]:
    """简单按句号/换行切分句子"""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


# ---------------------------------------------------------------------------
# diff-match-patch 差异提取
# ---------------------------------------------------------------------------

def diff_text_dmp(text1: str, text2: str) -> list[dict]:
    """
    使用 diff-match-patch 计算两段文本的差异。

    Returns:
        list[dict], 每个元素：
        {
            "type": "equal" | "insert" | "delete",
            "text": str,
        }
    """
    if _DMP is None:
        return diff_text_difflib(text1, text2)

    t1 = _clean_text(text1)
    t2 = _clean_text(text2)

    # chars level diff
    diffs = _DMP.diff_main(t1, t2)
    _DMP.diff_cleanupSemantic(diffs)

    result = []
    for op, text in diffs:
        if op == 0:      # equal
            result.append({"type": "equal", "text": text})
        elif op == -1:   # delete
            result.append({"type": "delete", "text": text})
        elif op == 1:    # insert
            result.append({"type": "insert", "text": text})
    return result


# ---------------------------------------------------------------------------
# difflib 备选
# ---------------------------------------------------------------------------

def diff_text_difflib(text1: str, text2: str) -> list[dict]:
    """使用 difflib.SequenceMatcher 行级 diff"""
    import difflib

    t1 = _clean_text(text1)
    t2 = _clean_text(text2)

    matcher = difflib.SequenceMatcher(None, t1.splitlines(), t2.splitlines())
    result = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            result.append({"type": "equal", "text": "\n".join(matcher.a[i1:i2])})
        elif tag == "delete":
            result.append({"type": "delete", "text": "\n".join(matcher.a[i1:i2])})
        elif tag == "insert":
            result.append({"type": "insert", "text": "\n".join(matcher.b[j1:j2])})
        elif tag == "replace":
            result.append({"type": "delete", "text": "\n".join(matcher.a[i1:i2])})
            result.append({"type": "insert", "text": "\n".join(matcher.b[j1:j2])})
    return result


# ---------------------------------------------------------------------------
# 变更检测
# ---------------------------------------------------------------------------

def has_significant_diff(diffs: list[dict], threshold_chars: int = 50) -> bool:
    """
    判断 diff 中是否存在显著变更。
    过滤掉琐碎变更（少于 threshold_chars 的增删）。
    """
    total_change = 0
    for d in diffs:
        if d["type"] != "equal":
            total_change += len(d["text"])
    return total_change >= threshold_chars


def extract_diff_snippet(diffs: list[dict], max_context: int = 200) -> str:
    """提取变更片段（含上下文），用于报告"""
    parts = []
    for d in diffs:
        if d["type"] == "equal":
            text = d["text"]
            if len(text) > max_context:
                text = text[:max_context] + " ..."
            parts.append(text)
        elif d["type"] == "delete":
            parts.append(f"~~{d['text']}~~")
        elif d["type"] == "insert":
            parts.append(f"**+{d['text']}**")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 按章节对执行 diff
# ---------------------------------------------------------------------------

def diff_aligned_sections(
    base_md: str,
    compare_md: str,
    alignment: dict,
) -> list[dict]:
    """
    根据对齐映射，对每对章节执行 diff。

    Args:
        base_md: Base 文档 Markdown
        compare_md: Compare 文档 Markdown
        alignment: aligner.py 输出的对齐结果

    Returns:
        list[dict]:
        {
            "base_section_id": str,
            "compare_section_id": str,
            "similarity": float,
            "has_diff": bool,
            "diff_summary": str,       # 变更摘要（变更字符数）
            "diff_snippet": str,       # 变更片段
            "base_only_sections": [dict],  # Base 独有章节
            "compare_only_sections": [dict],  # Compare 独有章节
        }
    ]
    """
    from src.aligner import extract_sections

    base_sections = extract_sections(base_md)
    compare_sections = extract_sections(compare_md)

    base_map = {s["id"]: s for s in base_sections}
    compare_map = {s["id"]: s for s in compare_sections}

    results = []

    # 对齐的章节对
    for align in alignment.get("alignments", []):
        base_id = align["base_id"]
        compare_id = align["compare_id"]

        base_sec = base_map.get(base_id, {})
        compare_sec = compare_map.get(compare_id, {})

        base_text = base_sec.get("content", "")
        compare_text = compare_sec.get("content", "")

        if not base_text and not compare_text:
            continue

        diffs = diff_text_dmp(base_text, compare_text)
        has_diff = has_significant_diff(diffs, threshold_chars=30)
        snippet = extract_diff_snippet(diffs) if has_diff else ""

        results.append({
            "base_section_id": base_id,
            "base_section_number": align.get("base_number", ""),
            "base_section_title": align.get("base_title", ""),
            "compare_section_id": compare_id,
            "compare_section_number": align.get("compare_number", ""),
            "compare_section_title": align.get("compare_title", ""),
            "similarity": align.get("similarity", 0),
            "has_diff": has_diff,
            "diff_summary": _summarize_diff(diffs),
            "diff_snippet": snippet,
            "base_content": base_text[:500],
            "compare_content": compare_text[:500],
            "base_page": base_sec.get("page_hint", "") or _extract_table_page_hint(base_text),
            "compare_page": compare_sec.get("page_hint", "") or _extract_table_page_hint(compare_text),
        })

    # Base 独有章节
    for sec in alignment.get("base_only", []):
        base_sec = base_map.get(sec["id"], {})
        results.append({
            "base_section_id": sec["id"],
            "base_section_number": sec.get("number", ""),
            "base_section_title": sec.get("title", ""),
            "compare_section_id": None,
            "compare_section_number": "",
            "compare_section_title": "",
            "similarity": 0,
            "has_diff": True,
            "diff_summary": "Base 独有章节",
            "diff_snippet": "",
            "base_content": base_sec.get("content", "")[:500],
            "compare_content": "",
            "base_page": base_sec.get("page_hint", "") or _extract_table_page_hint(base_sec.get("content", "")),
        })

    # Compare 独有章节
    for sec in alignment.get("compare_only", []):
        compare_sec = compare_map.get(sec["id"], {})
        results.append({
            "base_section_id": None,
            "base_section_number": "",
            "base_section_title": "",
            "compare_section_id": sec["id"],
            "compare_section_number": sec.get("number", ""),
            "compare_section_title": sec.get("title", ""),
            "similarity": 0,
            "has_diff": True,
            "diff_summary": "Compare 独有章节",
            "diff_snippet": "",
            "base_content": "",
            "compare_content": compare_sec.get("content", "")[:500],
            "compare_page": compare_sec.get("page_hint", "") or _extract_table_page_hint(compare_sec.get("content", "")),
        })

    return results


def _summarize_diff(diffs: list[dict]) -> str:
    """生成 diff 摘要"""
    del_chars = 0
    ins_chars = 0
    for d in diffs:
        if d["type"] == "delete":
            del_chars += len(d["text"])
        elif d["type"] == "insert":
            ins_chars += len(d["text"])
    if del_chars and ins_chars:
        return f"变更：删除 {del_chars} 字符，新增 {ins_chars} 字符"
    elif del_chars:
        return f"删除：{del_chars} 字符"
    elif ins_chars:
        return f"新增：{ins_chars} 字符"
    return "无显著变更"


# ---------------------------------------------------------------------------
# 调试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import logging

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    # 简单测试
    md1 = "# 1. Overview\n\n## 1.1 Scope\n\nThis document describes the protocol.\n## 1.2 References\n\n[1] Reference A"
    md2 = "# 1. Overview\n\n## 1.1 Scope\n\nThis document describes the enhanced protocol.\n## 1.3 New Section\n\nThis is new content."
    alignment = {
        "alignments": [
            {"base_id": "1", "base_number": "1", "base_title": "Overview",
             "compare_id": "1", "compare_number": "1", "compare_title": "Overview",
             "similarity": 0.95, "method": "tfidf_cosine"}
        ],
        "base_only": [{"id": "2", "number": "1.2", "title": "References"}],
        "compare_only": [{"id": "3", "number": "1.3", "title": "New Section"}],
    }
    results = diff_aligned_sections(md1, md2, alignment)
    print(json.dumps(results, indent=2, ensure_ascii=False))
