"""
章节对齐模块

基于 TF-IDF + 余弦相似度将 Base 文档的章节与 Compare 文档的章节进行匹配，
输出对齐映射表（alignment.json）。

流程：
  1. 提取各文档的标题行（Markdown # 标题）
  2. TF-IDF 向量化标题文本
  3. 余弦相似度矩阵
  4. 贪心匹配（每章节最多匹配一个对端章节）
  5. 未匹配章节标记为"独有"
"""

import json
import re
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("fh_protocol_compare.aligner")

# 目录行特征：数字/点开头 + 含 "...." 点线页码 + 总长度>40
DIR_LINE_RE = re.compile(r"^[\d.]+\s+.+\.{3,}\s*\d+$")


def _is_toc_line(line: str) -> bool:
    """判断一行是否为目录条目（TOC entry），若是则跳过不对齐。"""
    line = line.strip()
    return bool(DIR_LINE_RE.match(line)) if len(line) > 40 else False

# ---------------------------------------------------------------------------
# 核心功能关键词（用于跨标准语义对齐优先级）
# ---------------------------------------------------------------------------

CORE_KEYWORDS = [
    # U-plane 相关
    r"u[- ]?plane",
    r"user[- ]?plane",
    r"up[- ]?field",
    r"iq\s*data",
    r"user\s*plane",
    # C-plane 相关
    r"c[- ]?plane",
    r"control[- ]?plane",
    r"cp[- ]?field",
    r"control\s*plane",
    # 信道传输
    r"channel",
    r"transport",
    r"mapping",
    r"beam",
    r"antenna",
    # 消息结构
    r"message",
    r"ie\s*field",
    r"information\s*element",
    r"header",
    r"payload",
    # 定时同步
    r"timing",
    r"sync",
    r"synchronization",
    r"clock",
    # 协议控制
    r"framing",
    r"sequence",
    r"compression",
    r"crypt",
]

_CORE_KW_RE = re.compile(
    "|".join(CORE_KEYWORDS),
    re.IGNORECASE
)


def _extract_keywords(text: str) -> set:
    """从文本中提取核心关键词集合"""
    return set(_CORE_KW_RE.findall(text.lower()))


def _keyword_match_score(sec1: dict, sec2: dict) -> float:
    """
    计算两章节的关键词重叠得分（0.0 ~ 0.5）。
    关键词重叠越多，得分越高，用于 TF-IDF 的补充信号。
    """
    kw1 = _extract_keywords(sec1.get("title", "") + " " + sec1.get("content", "")[:500])
    kw2 = _extract_keywords(sec2.get("title", "") + " " + sec2.get("content", "")[:500])
    if not kw1 or not kw2:
        return 0.0
    overlap = len(kw1 & kw2)
    union = len(kw1 | kw2)
    # 关键词重叠比例 × 0.5 上限
    return round((overlap / union) * 0.5, 4) if union > 0 else 0.0

# ---------------------------------------------------------------------------
# 标题提取
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def extract_sections(markdown: str) -> list[dict]:
    """
    从 Markdown 中提取章节列表。

    Returns:
        list[dict]:
        {
            "id": str,          # 唯一标识，如 "1.2.3"
            "level": int,       # 标题层级 1-6
            "number": str,      # 编号部分，如 "1.2" 或 "Annex A"
            "title": str,       # 标题文本（不含编号）
            "raw": str,         # 原始行
            "page_hint": str,   # <!-- page=N --> 中的页码提示
            "content": str,     # 该标题到下一个同级标题之间的正文
        }
    """
    headings = []
    lines = markdown.split("\n")
    for line in lines:
        m = HEADING_RE.match(line)
        if not m:
            continue
        hashes, text = m.groups()
        level = len(hashes)

        # 提取编号（首位数字或字母）
        num_m = re.match(r"^([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*|[A-Z]\.?(?:\s|$)|Annex\s+[A-Z])\s*(.*)$", text.strip())
        if num_m:
            number = num_m.group(1).strip()
            title = num_m.group(2).strip()
        else:
            number = ""
            title = text.strip()

        # 页码提示（<!-- page=N --> 或 <!-- table_page=N -->）
        page_hint_m = re.search(r"<!--\s*(?:page|table_page)=(\d+)\s*-->", line)
        page_hint = page_hint_m.group(1) if page_hint_m else ""

        headings.append({
            "level": level,
            "number": number,
            "title": title,
            "raw": line.strip(),
            "page_hint": page_hint,
            "content": "",
        })

    # 填充 content（同级标题之间的内容）
    for i, sec in enumerate(headings):
        next_pos = i + 1
        end_pos = len(headings)
        for j in range(i + 1, len(headings)):
            if headings[j]["level"] == sec["level"]:
                end_pos = j
                break
        content_lines = []
        # 找 content 范围：markdown 中 raw 行之后到下一个同级标题之前
        raw_line = sec["raw"]
        raw_line_found = False
        for line in lines:
            if not raw_line_found:
                if line.strip() == raw_line:
                    raw_line_found = True
                continue
            lm = HEADING_RE.match(line.strip())
            if lm and len(lm.group(1)) == sec["level"]:
                break
            # 过滤目录行（TOC entry）
            if _is_toc_line(line):
                continue
            content_lines.append(line)
        sec["content"] = "\n".join(content_lines).strip()

    # 生成唯一 id
    for sec in headings:
        num = sec["number"].replace(".", "_").replace(" ", "_")
        if not num:
            num = f"untitled_{headings.index(sec)}"
        sec["id"] = num

    return headings


# ---------------------------------------------------------------------------
# TF-IDF 向量化
# ---------------------------------------------------------------------------

def _vectorize_tfidf(texts: list[str]) -> tuple:
    """TF-IDF 向量化，返回 (matrix, vectorizer, feature_names)"""
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        max_features=5000,
    )
    matrix = vectorizer.fit_transform(texts)
    return matrix, vectorizer


def _cosine_similarity_matrix(A, B):
    """计算 A (m×n) 和 B (n×k) 的余弦相似度矩阵 m×k"""
    from sklearn.metrics.pairwise import cosine_similarity
    return cosine_similarity(A, B)


# ---------------------------------------------------------------------------
# 贪心匹配
# ---------------------------------------------------------------------------

def align_sections(base_sections: list[dict], compare_sections: list[dict]) -> dict:
    """
    对齐两个文档的章节。

    Returns:
        dict:
        {
            "alignments": [
                {
                    "base_id": str,
                    "base_title": str,
                    "compare_id": str,
                    "compare_title": str,
                    "similarity": float,
                    "method": "tfidf_cosine",
                }
            ],
            "base_only": [base_section, ...],   # Base 独有
            "compare_only": [compare_section, ...],  # Compare 独有
        }
    """
    if not base_sections or not compare_sections:
        return {
            "alignments": [],
            "base_only": base_sections,
            "compare_only": compare_sections,
        }

    # 文本列表
    base_texts = [f"{s['number']} {s['title']}" for s in base_sections]
    compare_texts = [f"{s['number']} {s['title']}" for s in compare_sections]

    # TF-IDF
    base_vecs, vectorizer = _vectorize_tfidf(base_texts + compare_texts)
    n_base = len(base_texts)
    base_matrix = base_vecs[:n_base]
    compare_matrix = base_vecs[n_base:]

    sim_matrix = _cosine_similarity_matrix(base_matrix, compare_matrix)
    logger.debug(f"[Align] 相似度矩阵形状: {sim_matrix.shape}")

    # 合并 TF-IDF 相似度 + 关键词重叠得分
    # keyword_score 作为加成：有权重时 sim_final = sim + kw_score
    # 上限约束：sim_final 最多为 1.0
    sim_combined = sim_matrix.copy()
    for bi, bs in enumerate(base_sections):
        for ci, cs in enumerate(compare_sections):
            kw_score = _keyword_match_score(bs, cs)
            sim_combined[bi, ci] = min(1.0, sim_matrix[bi, ci] + kw_score)

    logger.debug(f"[Align] 关键词加成后的最大相似度: {sim_combined.max():.4f}")

    # 贪心匹配（优先匹配相似度最高的）
    matched_compare = set()
    alignments = []

    base_indices = list(range(len(base_sections)))
    # 按相似度降序遍历
    flat = []
    for i in range(sim_combined.shape[0]):
        for j in range(sim_combined.shape[1]):
            flat.append((i, j, sim_combined[i, j]))
    flat.sort(key=lambda x: x[2], reverse=True)

    # 阈值：低于 0.3 认为不匹配（跨标准文档可适当降低至 0.25）
    THRESHOLD = 0.25
    for bi, ci, sim in flat:
        if ci in matched_compare:
            continue
        if sim < THRESHOLD:
            continue
        matched_compare.add(ci)
        kw_score = _keyword_match_score(base_sections[bi], compare_sections[ci])
        method = "tfidf_cosine+keywords" if kw_score > 0 else "tfidf_cosine"
        alignments.append({
            "base_id": base_sections[bi]["id"],
            "base_title": base_sections[bi]["title"],
            "base_number": base_sections[bi]["number"],
            "compare_id": compare_sections[ci]["id"],
            "compare_title": compare_sections[ci]["title"],
            "compare_number": compare_sections[ci]["number"],
            "similarity": round(float(sim), 4),
            "tfidf_sim": round(float(sim_matrix[bi, ci]), 4),
            "keyword_score": kw_score,
            "method": method,
        })

    # 未匹配
    matched_base = {a["base_id"] for a in alignments}
    base_only = [s for s in base_sections if s["id"] not in matched_base]
    compare_only = [s for s in compare_sections if s["id"] not in matched_compare]

    result = {
        "alignments": alignments,
        "base_only": [
            {"id": s["id"], "number": s["number"], "title": s["title"]}
            for s in base_only
        ],
        "compare_only": [
            {"id": s["id"], "number": s["number"], "title": s["title"]}
            for s in compare_only
        ],
    }
    logger.info(f"[Align] 对齐完成：对齐 {len(alignments)} 对，Base 独有 {len(base_only)} 节，Compare 独有 {len(compare_only)} 节")
    return result


def align_markdown(base_md: str, compare_md: str) -> dict:
    """
    入口函数：对齐两个 Markdown 文本的章节。
    """
    base_sections = extract_sections(base_md)
    compare_sections = extract_sections(compare_md)
    return align_sections(base_sections, compare_sections)


def load_and_align(base_md_path: str, compare_md_path: str) -> dict:
    """从文件加载 Markdown 并对齐"""
    with open(base_md_path, "r", encoding="utf-8") as f:
        base_md = f.read()
    with open(compare_md_path, "r", encoding="utf-8") as f:
        compare_md = f.read()
    return align_markdown(base_md, compare_md)


# ---------------------------------------------------------------------------
# 调试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import logging

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    if len(sys.argv) >= 3:
        result = load_and_align(sys.argv[1], sys.argv[2])
    else:
        # 简单测试
        md1 = "# 1. Overview\n\nIntro text\n## 1.1 Scope\n\nScope text\n## 1.2 References\n\nRef text"
        md2 = "# 1. Overview\n\nIntro text changed\n## 1.1 Scope\n\nNew scope text\n### 1.1.1 Details\n\nDetail text\n## 2. Architecture\n\nArch text"
        result = align_markdown(md1, md2)

    print(json.dumps(result, indent=2, ensure_ascii=False))
