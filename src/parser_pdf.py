"""
PDF 解析模块

将 PDF 文档解析为结构化 Markdown（保留章节层级、段落定位信息）。
支持：pdfplumber（主力）+ pdfminer.six（备选）+ pypdfium2（图片 OCR 辅助）。
"""

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("fh_protocol_compare.parser_pdf")


# ---------------------------------------------------------------------------
# 章节标题识别
# ---------------------------------------------------------------------------

# 常见协议文档章节格式
SECTION_PATTERNS = [
    re.compile(r"^(\d+)\.\s+(.+)$"),                          # 1. Introduction
    re.compile(r"^(\d+)\.(\d+)\s+(.+)$"),                     # 4.1 Signal Flow
    re.compile(r"^(\d+)\.(\d+)\.(\d+)\s+(.+)$"),             # 4.1.2 Message Structure
    re.compile(r"^(\d+)\.(\d+)\.(\d+)\.(\d+)\s+(.+)$"),      # 深度子节
    re.compile(r"^(Annex|Appendix)\s+([A-Z])\.\s+(.+)$", re.I),  # Annex A. Scope
    re.compile(r"^([A-Z])\.\s+(.+)$"),                         # A. Overview
]

# 需要过滤的页眉页脚行（常见 3GPP 格式）
HEADER_FOOTER_PATTERNS = [
    re.compile(r"^3GPP\s+TS\s+\d+\.\d+"),
    re.compile(r"^\d+\s+Release\s+\d+", re.I),
    re.compile(r"^Version\s+\d+-\d+-\d+"),
    re.compile(r"^Page\s+\d+\s+of\s+\d+", re.I),
]


def _is_header_footer(line: str) -> bool:
    line = line.strip()
    if not line or len(line) < 3:
        return True
    for pat in HEADER_FOOTER_PATTERNS:
        if pat.match(line):
            return True
    return False


def _parse_title_level(line: str) -> Optional[tuple[int, str]]:
    """解析标题行，返回 (level, title) 或 None"""
    line = line.strip()
    for pat in SECTION_PATTERNS:
        m = pat.match(line)
        if m:
            groups = m.groups()
            if len(groups) == 2:          # Annex A / A. xxx
                return 1, groups[1].strip()
            elif len(groups) == 3:        # 1. xxx / Annex A. xxx
                level = 1 if groups[0].lower() in ("annex", "appendix") else int(groups[0])
                return level, groups[2].strip()
            elif len(groups) == 4:        # 1.1 xxx
                return int(groups[0]), groups[3].strip()
            elif len(groups) == 5:        # 1.1.1 xxx
                return int(groups[0]), groups[4].strip()
    return None


# ---------------------------------------------------------------------------
# 核心解析函数
# ---------------------------------------------------------------------------

def extract_text_pdfplumber(pdf_path: str) -> list[dict]:
    """
    使用 pdfplumber 提取 PDF 文本。

    Returns:
        list[dict], 每个元素代表一页：
        {
            "page_num": int,
            "text": str,          # 原始文本
            "chars": list,       # pdfplumber char 对象列表（用于定位）
        }
    """
    import pdfplumber

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            p = page.extract_text()
            chars = page.chars if hasattr(page, "chars") else []
            pages.append({
                "page_num": i + 1,
                "text": p or "",
                "chars": chars,
            })
            logger.debug(f"[pdfplumber] page {i+1}: {len(p or '')} chars")
    return pages


def extract_tables_pdfplumber(pdf_path: str) -> list[dict]:
    """
    使用 pdfplumber 提取表格。

    Returns:
        list[dict]:
        {
            "page_num": int,
            "table_index": int,
            "table": list[list[str]],
        }
    """
    import pdfplumber

    tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            raw_tables = page.extract_tables()
            if raw_tables:
                for j, tbl in enumerate(raw_tables):
                    if tbl:
                        tables.append({
                            "page_num": i + 1,
                            "table_index": j,
                            "table": tbl,
                        })
    return tables


def extract_text_pdfminer(pdf_path: str) -> list[dict]:
    """
    使用 pdfminer.six 提取 PDF 文本（备选方案，用于复杂布局）。

    Returns: 同 extract_text_pdfplumber
    """
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer

    pages = []
    for i, page_layout in enumerate(extract_pages(pdf_path)):
        texts = []
        for element in page_layout:
            if isinstance(element, LTTextContainer):
                t = element.get_text().strip()
                if t:
                    texts.append(t)
        full_text = "\n".join(texts)
        pages.append({
            "page_num": i + 1,
            "text": full_text,
            "chars": [],
        })
        logger.debug(f"[pdfminer] page {i+1}: {len(full_text)} chars")
    return pages


def to_structured_markdown(
    pages: list[dict],
    *,
    min_para_len: int = 20,
    preserve_page_breaks: bool = True,
) -> str:
    """
    将提取的页面列表转换为带章节层级的 Markdown。

    - 识别标题行，生成 # ## ### 层级的 Markdown 标题
    - 过滤页眉页脚
    - 保留段落和表格原始内容
    """
    lines_out = []
    current_h1 = ""
    current_h2 = ""
    current_h3 = ""

    for page in pages:
        page_num = page["page_num"]
        raw_text = page["text"]
        if not raw_text:
            continue

        for raw_line in raw_text.split("\n"):
            line = raw_line.strip()
            if not line or _is_header_footer(line):
                continue

            parsed = _parse_title_level(line)
            if parsed:
                level, title = parsed
                if level == 1:
                    current_h1 = title
                    current_h2 = ""
                    current_h3 = ""
                    lines_out.append(f"# {title}\n")
                    lines_out.append(f"<!-- page={page_num} -->\n")
                elif level == 2:
                    current_h2 = title
                    current_h3 = ""
                    lines_out.append(f"## {title}\n")
                    lines_out.append(f"<!-- page={page_num} -->\n")
                elif level == 3:
                    current_h3 = title
                    lines_out.append(f"### {title}\n")
                    lines_out.append(f"<!-- page={page_num} -->\n")
                else:
                    lines_out.append(f"#### {title}\n")
                    lines_out.append(f"<!-- page={page_num} -->\n")
            else:
                # 普通段落
                if len(line) >= min_para_len:
                    lines_out.append(f"{line}\n\n")

        if preserve_page_breaks:
            lines_out.append(f"\n<!-- PAGE BREAK {page_num} -->\n")

    return "".join(lines_out)


def parse_pdf(
    pdf_path: str,
    *,
    use_miner_fallback: bool = True,
) -> tuple[str, list[dict]]:
    """
    解析 PDF 文档的主入口。

    优先使用 pdfplumber，若提取结果异常（文本过少），自动回退到 pdfminer。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        (structured_markdown: str, raw_pages: list[dict])
    """
    import os

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    logger.info(f"[PDF] 开始解析: {pdf_path}")
    try:
        pages = extract_text_pdfplumber(pdf_path)
        total_chars = sum(len(p["text"]) for p in pages)
        logger.info(f"[PDF] pdfplumber 提取完成，共 {len(pages)} 页，{total_chars} 字符")

        if total_chars < 100 and use_miner_fallback:
            logger.warning("[PDF] pdfplumber 结果过少，切换至 pdfminer")
            pages = extract_text_pdfminer(pdf_path)
            total_chars = sum(len(p["text"]) for p in pages)
            logger.info(f"[PDF] pdfminer 提取完成，共 {len(pages)} 页，{total_chars} 字符")

    except ImportError as e:
        logger.error(f"[PDF] 缺少依赖: {e}")
        raise

    md = to_structured_markdown(pages)
    return md, pages


# ---------------------------------------------------------------------------
# 调试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    if len(sys.argv) < 2:
        print("用法: python -m src.parser_pdf <pdf_path>")
        sys.exit(1)

    md, pages = parse_pdf(sys.argv[1])
    print(md[:3000])
