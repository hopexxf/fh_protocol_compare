"""
test_parser.py — Phase 2：文档解析测试

TC2-1：pdfplumber 提取（小 PDF）
TC2-2：pdfplumber 提取（大 PDF）
TC2-3：Markdown 结构化输出
TC2-4：pdfminer 回退
TC2-5：自动识别解析器
TC2-6：文件不存在

注：fixture 注入在 pytest 9.x/Windows 下存在已知问题，
所有路径使用硬编码常量，不依赖 conftest.py 的 fixture。
"""

import os
import pytest
from pathlib import Path

from src.parser_pdf import (
    extract_text_pdfplumber,
    extract_text_pdfminer,
    to_structured_markdown,
    parse_pdf,
)
from src.parser_docx import parse_docx


# ------------------------------------------------------------------
# 硬编码路径常量（与 conftest.py 保持一致）
# ------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ASTRI_PDF = str(PROJECT_ROOT / "input" / "compare" / "ASTRI_NRBS_L1_v0.2.0_rc1_docs_PHY_Architecture_Design_ASTRI_0003191_NR_CRAN_RRU_Design_VS1.pdf")
ORAN_PDF = str(PROJECT_ROOT / "input" / "base" / "O-RAN.WG4.CUS.0-v05.00.pdf")

_ASTRI_EXISTS = Path(ASTRI_PDF).exists()
_ORAN_EXISTS = Path(ORAN_PDF).exists()


def _skip(msg):
    """返回 skipif 条件，在 class body 内求值时 _*_EXISTS 已定义"""
    return pytest.mark.skipif(False, reason=msg)


# ------------------------------------------------------------------
# TC2-1：pdfplumber 提取（小 PDF）
# ------------------------------------------------------------------

class TestPdfplumberExtract:
    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_extract_pages_astri(self):
        pages = extract_text_pdfplumber(ASTRI_PDF)
        assert len(pages) > 0
        for p in pages:
            assert "page_num" in p
            assert "text" in p
            assert isinstance(p["text"], str)
        total_chars = sum(len(p.get("text", "")) for p in pages)
        assert total_chars > 1000, f"总字符数应 > 1000，实际 {total_chars}"

    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_extract_pages_returns_list(self):
        pages = extract_text_pdfplumber(ASTRI_PDF)
        assert isinstance(pages, list)


# ------------------------------------------------------------------
# TC2-2：pdfplumber 提取（大 PDF）
# ------------------------------------------------------------------

class TestPdfplumberLarge:
    @pytest.mark.skipif(not _ORAN_EXISTS, reason="O-RAN PDF 不存在")
    def test_extract_pages_oaran(self):
        pages = extract_text_pdfplumber(ORAN_PDF)
        assert len(pages) > 50, f"O-RAN 页数应 > 50，实际 {len(pages)}"
        assert all("text" in p for p in pages)


# ------------------------------------------------------------------
# TC2-3：Markdown 结构化输出
# ------------------------------------------------------------------

class TestMarkdownStructure:
    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_structured_markdown_has_headers(self):
        pages = extract_text_pdfplumber(ASTRI_PDF)
        md = to_structured_markdown(pages)
        assert "# " in md
        assert md.count("\n") > 10

    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_structured_markdown_has_page_hints(self):
        pages = extract_text_pdfplumber(ASTRI_PDF)
        md = to_structured_markdown(pages)
        assert "<!-- page=" in md

    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_structured_markdown_length(self):
        pages = extract_text_pdfplumber(ASTRI_PDF)
        md = to_structured_markdown(pages)
        assert len(md) > 5000, f"Markdown 长度应 > 5000，实际 {len(md)}"

    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_structured_markdown_no_excessive_whitespace(self):
        pages = extract_text_pdfplumber(ASTRI_PDF)
        md = to_structured_markdown(pages)
        assert "\n\n\n\n" not in md


# ------------------------------------------------------------------
# TC2-4：pdfminer 回退
# ------------------------------------------------------------------

class TestPdfminerFallback:
    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_extract_pages_miner(self):
        pages = extract_text_pdfminer(ASTRI_PDF)
        assert isinstance(pages, list)
        assert len(pages) > 0
        for p in pages:
            assert "page_num" in p
            assert "text" in p


# ------------------------------------------------------------------
# TC2-5：自动识别解析器
# ------------------------------------------------------------------

class TestAutoDetectParser:
    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_parse_pdf_returns_tuple(self):
        """parse_pdf 返回 (markdown: str, raw_pages: list[dict])"""
        md, pages = parse_pdf(ASTRI_PDF)
        assert isinstance(md, str)
        assert isinstance(pages, list)
        assert len(pages) > 0
        # 每页应有 page_num 和 text
        assert all("page_num" in p and "text" in p for p in pages)

    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_parse_pdf_markdown_not_empty(self):
        """Markdown 输出非空"""
        md, pages = parse_pdf(ASTRI_PDF)
        assert len(md) > 1000, f"Markdown 应有 > 1000 字符，实际 {len(md)}"
        assert "<!-- page=" in md, "应包含页码标记"

    @pytest.mark.skipif(not _ASTRI_EXISTS, reason="ASTRI PDF 不存在")
    def test_parse_pdf_page_count(self):
        """页数与原始 PDF 匹配（ASTRI ~27 页）"""
        _, pages = parse_pdf(ASTRI_PDF)
        assert 20 <= len(pages) <= 35, f"ASTRI 页数应在 20~35 之间，实际 {len(pages)}"


# ------------------------------------------------------------------
# TC2-6：文件不存在
# ------------------------------------------------------------------

class TestParserErrorHandling:
    def test_parse_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            parse_pdf("C:\\nonexistent_file_12345.pdf")

    def test_extract_pages_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            extract_text_pdfplumber("C:\\nonexistent_file_12345.pdf")
