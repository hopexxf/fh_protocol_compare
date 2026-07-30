"""
conftest.py — pytest 全局 fixtures
"""

import os
import sys
from pathlib import Path

import pytest

# 确保 src 在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def project_root():
    return PROJECT_ROOT


@pytest.fixture
def sample_base_dir(project_root):
    return project_root / "input" / "base"


@pytest.fixture
def sample_compare_dir(project_root):
    return project_root / "input" / "compare"


@pytest.fixture
def astri_pdf_path(sample_compare_dir):
    """ASTRI 文档路径（Compare 目录有样本）"""
    files = list(sample_compare_dir.glob("*.pdf"))
    if not files:
        pytest.skip("ASTRI PDF 样本文件不存在")
    return str(files[0])


@pytest.fixture
def oran_pdf_path(sample_base_dir):
    """O-RAN 大文档路径"""
    files = list(sample_base_dir.glob("*.pdf"))
    if not files:
        pytest.skip("O-RAN PDF 样本文件不存在")
    return str(files[0])
