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


def extract_tables_camelot(pdf_path: str, flavor: str = "stream") -> list[dict]:
    """
    使用 camelot 提取表格（高精度）。

    Args:
        pdf_path: PDF 文件路径
        flavor: 'lattice'（有格线，需要 Ghostscript）或 'stream'（无格线，无需 Ghostscript）

    Returns:
        list[dict]: [
            {
                "page_num": int,
                "table_index": int,
                "table": list[list[str]],  # 二维数组
                "accuracy": float,         # 提取准确度 (0-100)
                "bbox": tuple,             # 表格边界 (x1, y1, x2, y2)
            },
            ...
        ]
    """
    import camelot

    logger.info(f"[Camelot] 开始提取表格: {pdf_path}, flavor={flavor}")

    try:
        tables = camelot.read_pdf(pdf_path, pages='all', flavor=flavor)

        results = []
        for i, t in enumerate(tables):
            results.append({
                "page_num": int(t.page),
                "table_index": i,
                "table": t.df.values.tolist(),
                "accuracy": float(t.accuracy),
                "bbox": t._bbox if hasattr(t, '_bbox') else None,
            })

        logger.info(f"[Camelot] 提取完成，共 {len(results)} 个表格")
        return results

    except Exception as e:
        logger.error(f"[Camelot] 提取失败: {e}")
        raise


def _table_to_markdown(table_data: list[list[str]], caption: str = "") -> str:
    """
    将单个表格转换为 Markdown 格式。

    Args:
        table_data: 二维数组（含表头）
        caption: 表格标题（可选）

    Returns:
        Markdown 格式的表格字符串
    """
    if not table_data or len(table_data) < 1:
        return ""

    lines = []

    # 表格标题
    if caption:
        lines.append(f"**{caption}**\n")

    # 表头
    header = table_data[0]
    lines.append("| " + " | ".join(str(cell).strip().replace("\n", " ") for cell in header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")

    # 表体
    for row in table_data[1:]:
        # 处理空单元格和换行
        cells = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines) + "\n"


def _tables_to_markdown(tables: list[dict]) -> str:
    """
    将表格列表转换为 Markdown 格式（批量）。

    每个表格标注页码和准确度，便于溯源。
    """
    if not tables:
        return ""

    lines = ["\n\n---\n\n## 表格列表\n"]

    for tbl in tables:
        # 章节信息（Phase 2 会填充）
        section_info = tbl.get("section_title", "")
        if section_info:
            caption = f"表格 {tbl['table_index']+1}（{section_info}，P{tbl['page_num']}）"
        else:
            caption = f"表格 {tbl['page_num']}-{tbl['table_index']}（P{tbl['page_num']}）"

        # 准确度信息
        if "accuracy" in tbl:
            caption += f"，准确度 {tbl['accuracy']:.1f}%"

        lines.append(f"\n### {caption}\n\n")
        lines.append(_table_to_markdown(tbl["table"]))
        lines.append(f"\n<!-- table_page={tbl['page_num']} table_index={tbl['table_index']} -->\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 2: 上下文保留（表格插回章节）
# ---------------------------------------------------------------------------

def _parse_sections_from_markdown(md: str) -> list[dict]:
    """
    从 Markdown 解析章节信息（基于 <!-- page=N --> 标记）。

    Returns:
        [
            {
                "section_id": "4.1.2",
                "title": "Message Structure",
                "level": 3,
                "start_line": 123,
                "end_line": 200,
                "page_num": 15,
            },
            ...
        ]
    """
    import re

    sections = []
    lines = md.split("\n")

    # 章节标题正则
    section_pattern = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+<!-- page=(\d+) -->)?$")
    page_pattern = re.compile(r"<!-- page=(\d+) -->")

    current_section = None
    current_page = None

    for i, line in enumerate(lines):
        # 检测页码标记
        page_match = page_pattern.search(line)
        if page_match:
            current_page = int(page_match.group(1))

        # 检测章节标题
        section_match = section_pattern.match(line)
        if section_match:
            # 保存上一个章节
            if current_section:
                current_section["end_line"] = i - 1
                sections.append(current_section)

            # 开始新章节
            level = len(section_match.group(1))
            title = section_match.group(2).strip()

            # 提取章节编号
            id_match = re.match(r"^([\d.]+|Annex\s+[A-Z]|[A-Z])", title)
            section_id = id_match.group(1) if id_match else f"h{level}"

            current_section = {
                "section_id": section_id,
                "title": title,
                "level": level,
                "start_line": i,
                "end_line": None,
                "page_num": current_page,
            }

    # 最后一个章节
    if current_section:
        current_section["end_line"] = len(lines) - 1
        sections.append(current_section)

    logger.debug(f"[Phase2] 解析到 {len(sections)} 个章节")
    return sections


def _associate_tables_with_sections(tables: list[dict], sections: list[dict], md: str) -> list[dict]:
    """
    将表格关联到对应章节。

    关联策略：
    1. 页码匹配：表格所在页 = 章节所在页
    2. 文本匹配（辅助）：表格内容与章节文本相似度

    Returns:
        更新后的表格列表，每个表格新增 section_id/section_title/context_before
    """
    import re

    lines = md.split("\n")

    for tbl in tables:
        page = tbl["page_num"]

        # 策略 1：页码匹配
        matched_sections = [s for s in sections if s["page_num"] == page]

        if len(matched_sections) == 1:
            # 唯一匹配
            section = matched_sections[0]
        elif len(matched_sections) > 1:
            # 多个匹配，选最后一个（表格通常在章节末尾）
            section = matched_sections[-1]
        else:
            # 无匹配，找最近的章节
            section = _find_best_section_by_text(tbl, sections, lines)

        if section:
            tbl["section_id"] = section["section_id"]
            tbl["section_title"] = section["title"]
            tbl["section_start_line"] = section["start_line"]
            tbl["section_end_line"] = section["end_line"]

            # 提取上下文（表格前 50 字符）
            if "bbox" in tbl and tbl["bbox"]:
                # 依赖 PDF 坐标，暂不实现
                tbl["context_before"] = ""
            else:
                tbl["context_before"] = ""
        else:
            # 无法关联
            tbl["section_id"] = "unknown"
            tbl["section_title"] = ""
            tbl["context_before"] = ""

    logger.info(f"[Phase2] 表格关联完成，{len([t for t in tables if t.get('section_id') != 'unknown'])}/{len(tables)} 成功")
    return tables


def _find_best_section_by_text(tbl: dict, sections: list[dict], lines: list[str]) -> dict:
    """
    文本匹配辅助：根据表格内容找最相似章节。

    简化实现：返回页码最接近的章节。
    """
    page = tbl["page_num"]

    # 找页码最接近的章节
    closest = None
    min_diff = float("inf")

    for section in sections:
        diff = abs(section["page_num"] - page) if section.get("page_num") else float("inf")
        if diff < min_diff:
            min_diff = diff
            closest = section

    return closest


def _insert_tables_into_sections(md: str, tables: list[dict]) -> str:
    """
    将表格插回对应章节末尾。

    插入策略：从后向前插入，避免行号偏移。

    Returns:
        更新后的 Markdown
    """
    lines = md.split("\n")

    # 按章节分组
    section_tables = {}
    for tbl in tables:
        section_id = tbl.get("section_id", "unknown")
        if section_id not in section_tables:
            section_tables[section_id] = []
        section_tables[section_id].append(tbl)

    # 从后向前插入
    insertions = []

    for section_id, tbls in section_tables.items():
        if section_id == "unknown":
            continue

        # 找到章节末尾
        first_tbl = tbls[0]
        if "section_end_line" not in first_tbl:
            continue

        end_line = first_tbl["section_end_line"]

        # 生成表格 Markdown
        table_md_parts = []
        for tbl in tbls:
            caption = f"表格 {tbl.get('section_title', '')}（P{tbl['page_num']}）"
            if "accuracy" in tbl:
                caption += f"，准确度 {tbl['accuracy']:.1f}%"

            table_md_parts.append(f"\n\n**{caption}**\n\n")
            table_md_parts.append(_table_to_markdown(tbl["table"]))
            table_md_parts.append(f"\n<!-- TABLE: page={tbl['page_num']} index={tbl['table_index']} -->\n")

        table_md = "".join(table_md_parts)
        insertions.append((end_line, table_md))

    # 按行号降序排序（从后向前插入）
    insertions.sort(key=lambda x: x[0], reverse=True)

    # 执行插入
    for line_num, table_md in insertions:
        lines.insert(line_num + 1, table_md)

    result = "\n".join(lines)
    logger.info(f"[Phase2] 表格插入完成，{len(insertions)} 个章节包含表格")

    return result


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
    extract_tables: bool = True,
    insert_tables_to_sections: bool = False,  # Phase 2 实现
) -> tuple[str, list[dict], list[dict]]:
    """
    解析 PDF 文档的主入口。

    优先使用 pdfplumber 提取文本，若结果异常则回退到 pdfminer。
    支持表格提取（camelot 优先，pdfplumber 兜底）。

    Args:
        pdf_path: PDF 文件路径
        use_miner_fallback: 是否在 pdfplumber 失败时回退到 pdfminer
        extract_tables: 是否启用表格提取
        insert_tables_to_sections: 是否将表格插回章节（Phase 2）

    Returns:
        (structured_markdown: str, raw_pages: list[dict], tables: list[dict])
    """
    import os

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    logger.info(f"[PDF] 开始解析: {pdf_path}")

    # 1. 提取文本
    try:
        pages = extract_text_pdfplumber(pdf_path)
        total_chars = sum(len(p["text"]) for p in pages)
        logger.info(f"[PDF] 文本提取完成，{len(pages)} 页，{total_chars} 字符")

        if total_chars < 100 and use_miner_fallback:
            logger.warning("[PDF] pdfplumber 结果过少，切换至 pdfminer")
            pages = extract_text_pdfminer(pdf_path)
            total_chars = sum(len(p["text"]) for p in pages)
            logger.info(f"[PDF] pdfminer 提取完成，{len(pages)} 页，{total_chars} 字符")

    except ImportError as e:
        logger.error(f"[PDF] 缺少依赖: {e}")
        raise

    # 2. 转换为 Markdown
    md = to_structured_markdown(pages)

    # 3. 提取表格
    tables = []
    if extract_tables:
        tables = _extract_tables_with_config(pdf_path, md, pages)

        # 4. 处理表格
        if tables:
            if insert_tables_to_sections:
                # Phase 2：插回章节
                logger.info("[PDF] 启用 Phase 2：表格插回章节")
                sections = _parse_sections_from_markdown(md)
                tables = _associate_tables_with_sections(tables, sections, md)
                md = _insert_tables_into_sections(md, tables)
            else:
                # Phase 1：追加到文档末尾
                md += _tables_to_markdown(tables)

    return md, pages, tables


def _extract_tables_with_config(pdf_path: str, md: str, pages: list[dict]) -> list[dict]:
    """
    根据配置提取表格（camelot 优先，pdfplumber 兜底）。

    Returns:
        表格列表，每个元素包含：
        {
            "page_num": int,
            "table_index": int,
            "table": list[list[str]],
            "accuracy": float (camelot),
            "bbox": tuple (camelot),
        }
    """
    from src.config_loader import get_config

    config = get_config()
    pdf_config = config.get("pdf", {})

    use_camelot = pdf_config.get("use_camelot", True)
    flavor = pdf_config.get("camelot_flavor", "stream")
    min_accuracy = pdf_config.get("table_min_accuracy", 80)
    fallback = pdf_config.get("fallback_to_pdfplumber", True)

    tables = []

    if use_camelot:
        try:
            tables = extract_tables_camelot(pdf_path, flavor=flavor)

            # 检查准确度
            low_accuracy = [t for t in tables if t.get("accuracy", 100) < min_accuracy]
            if low_accuracy:
                logger.warning(f"[PDF] {len(low_accuracy)} 个表格准确度低于 {min_accuracy}%")

            logger.info(f"[PDF] Camelot 表格提取完成，{len(tables)} 个表格")

        except Exception as e:
            logger.warning(f"[PDF] Camelot 提取失败: {e}")
            if fallback:
                logger.info("[PDF] 回退到 pdfplumber 表格提取")
                tables = extract_tables_pdfplumber(pdf_path)
                logger.info(f"[PDF] pdfplumber 表格提取完成，{len(tables)} 个表格")
            else:
                logger.error("[PDF] 表格提取失败，未启用降级")
                tables = []
    else:
        tables = extract_tables_pdfplumber(pdf_path)
        logger.info(f"[PDF] pdfplumber 表格提取完成，{len(tables)} 个表格")

    return tables


# ---------------------------------------------------------------------------
# 调试入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")
    if len(sys.argv) < 2:
        print("用法: python -m src.parser_pdf <pdf_path>")
        sys.exit(1)

    md, pages, tables = parse_pdf(sys.argv[1])
    print(md[:3000])
    print(f"\n\n表格数量: {len(tables)}")
    for t in tables[:3]:
        print(f"  P{t['page_num']}: {len(t['table'])} rows, accuracy={t.get('accuracy', 'N/A')}")
