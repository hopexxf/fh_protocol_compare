"""思路 1 Boilerplate 过滤 — 单元测试 + 本地测量（零 LLM）"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.filter_boilerplate import (
    is_boilerplate_section,
    filter_diff_items,
    filter_alignment,
    count_boilerplate,
)


def _item(base_title="", compare_title="", base_content="", compare_content="",
          base_page=None, compare_page=None):
    return {
        "base_section_title": base_title,
        "compare_section_title": compare_title,
        "base_content": base_content,
        "compare_content": compare_content,
        "base_page": base_page,
        "compare_page": compare_page,
    }


# ---- is_boilerplate_section ----
def test_title_blacklist_english():
    assert is_boilerplate_section("References", "") is True
    assert is_boilerplate_section("List of Tables", "") is True


def test_title_blacklist_chinese():
    assert is_boilerplate_section("目录", "") is True
    assert is_boilerplate_section("修订历史", "") is True


def test_content_signal_copyright():
    assert is_boilerplate_section("", "© 2024 All rights reserved.") is True


def test_functional_not_flagged():
    assert is_boilerplate_section("U-plane Message Structure", "eCPRI header format") is False


def test_custom_keywords():
    cfg = {"boilerplate_keywords": ["appendix"]}
    assert is_boilerplate_section("Appendix A", "", cfg) is True


# ---- filter_diff_items ----
def test_filter_disabled_passthrough():
    items = [_item(base_title="References", compare_title="References")]
    assert filter_diff_items(items, {"enabled": False}) is items


def test_filter_aligned_pair_dropped_if_either_boilerplate():
    items = [
        _item(base_title="References", compare_title="Scope"),   # base boilerplate
        _item(base_title="Scope", compare_title="References"),   # compare boilerplate
        _item(base_title="U-plane", compare_title="U-plane"),    # functional
    ]
    out = filter_diff_items(items, {"enabled": True, "skip_boilerplate": True})
    assert len(out) == 1
    assert out[0]["base_section_title"] == "U-plane"


def test_filter_base_only_dropped():
    items = [
        _item(base_title="Contents"),
        _item(base_title="C-plane Section Type"),
    ]
    out = filter_diff_items(items, {"enabled": True, "skip_boilerplate": True})
    assert [i["base_section_title"] for i in out] == ["C-plane Section Type"]


def test_filter_compare_only_dropped():
    items = [
        _item(compare_title="Glossary"),
        _item(compare_title="FFT Size"),
    ]
    out = filter_diff_items(items, {"enabled": True, "skip_boilerplate": True})
    assert [i["compare_section_title"] for i in out] == ["FFT Size"]


def test_skip_front_pages():
    items = [
        _item(base_title="Foreword", base_page=1),
        _item(base_title="U-plane", base_page=2),
        _item(compare_title="FFT Size", compare_page=3),
    ]
    out = filter_diff_items(
        items, {"enabled": True, "skip_boilerplate": True, "skip_front_pages": 1}
    )
    assert all(i.get("base_page") != 1 for i in out)
    assert len(out) == 2


def test_filter_does_not_mutate_input():
    items = [_item(base_title="References", compare_title="References")]
    filter_diff_items(items, {"enabled": True})
    assert items[0]["base_section_title"] == "References"


# ---- filter_alignment ----


def _sec(title, content="", page_hint=""):
    return {"id": title.lower().replace(" ", "-"), "title": title,
            "content": content, "page_hint": page_hint}


def _align(base_t, compare_t, base_c="", compare_c=""):
    return {"base_section": _sec(base_t, base_c), "compare_section": _sec(compare_t, compare_c)}


def _alignment(alignments=None, base_only=None, compare_only=None):
    return {
        "alignments": alignments or [],
        "base_only": base_only or [],
        "compare_only": compare_only or [],
    }


def test_filter_alignment_disabled_passthrough():
    inp = _alignment(
        alignments=[_align("References", "Scope")],
        base_only=[_sec("Contents")],
    )
    out = filter_alignment(inp, {"enabled": False})
    assert out is inp  # 同一对象


def test_filter_alignment_aligned_pair_dropped():
    inp = _alignment(alignments=[
        _align("References", "References"),   # 两端均 boilerplate
        _align("U-plane", "U-plane"),         # 功能
        _align("Foreword", "Foreword"),       # boilerplate
    ])
    out = filter_alignment(inp, {"enabled": True, "skip_boilerplate": True})
    assert len(out["alignments"]) == 1
    assert out["alignments"][0]["base_section"]["title"] == "U-plane"


def test_filter_alignment_aligned_pair_either_side_dropped():
    inp = _alignment(alignments=[
        _align("Contents", "FFT Size"),      # base boilerplate
        _align("FFT Size", "References"),    # compare boilerplate
        _align("C-plane", "C-plane"),        # 功能
    ])
    out = filter_alignment(inp, {"enabled": True, "skip_boilerplate": True})
    assert len(out["alignments"]) == 1


def test_filter_alignment_base_only_dropped():
    inp = _alignment(base_only=[
        _sec("Contents"),
        _sec("Glossary"),
        _sec("U-plane"),
    ])
    out = filter_alignment(inp, {"enabled": True, "skip_boilerplate": True})
    assert [s["title"] for s in out["base_only"]] == ["U-plane"]


def test_filter_alignment_compare_only_dropped():
    inp = _alignment(compare_only=[
        _sec("References"),
        _sec("Index"),
        _sec("FFT Size"),
    ])
    out = filter_alignment(inp, {"enabled": True, "skip_boilerplate": True})
    assert [s["title"] for s in out["compare_only"]] == ["FFT Size"]


def test_filter_alignment_does_not_mutate_input():
    inp = _alignment(base_only=[_sec("Contents")], compare_only=[_sec("References")])
    filter_alignment(inp, {"enabled": True})
    assert len(inp["base_only"]) == 1
    assert len(inp["compare_only"]) == 1


def test_filter_alignment_preserves_structure_keys():
    """filter_alignment 返回的结构与 diff_aligned_sections 兼容（alignments/base_only/compare_only）。"""
    inp = _alignment(
        alignments=[_align("U-plane", "U-plane")],
        base_only=[_sec("C-plane")],
        compare_only=[_sec("FFT Size")],
    )
    out = filter_alignment(inp, {"enabled": True, "skip_boilerplate": True})
    assert set(out.keys()) == {"alignments", "base_only", "compare_only"}


# ---- count_boilerplate ----
def test_count_boilerplate():
    items = [
        _item(base_title="Contents"),
        _item(base_title="U-plane"),
        _item(base_title="References"),
    ]
    c = count_boilerplate(items)
    assert c["total"] == 3
    assert c["title_hits"] == 2  # Contents + References


# ---- 本地测量：基于历史归档的 base_spec / compare_spec ----
def _find_dir(pattern):
    root = Path(__file__).resolve().parent.parent / "versions"
    if not root.exists():
        return None
    matches = sorted(root.glob(pattern))
    return matches[0] if matches else None


def test_measurement_boilerplate_rate():
    """本地测量真实 boilerplate 占比（不调 LLM）。无历史数据时 skip。"""
    from src.aligner import extract_sections

    version_dir = _find_dir("20260801_*")
    base_spec = version_dir / "base_spec.md" if version_dir else None
    compare_spec = version_dir / "compare_spec.md" if version_dir else None
    if not (base_spec and compare_spec and base_spec.exists() and compare_spec.exists()):
        pytest.skip("历史归档 base_spec/compare_spec 缺失，跳过本地测量")

    cfg = {"enabled": True, "skip_boilerplate": True}
    total = 0
    flagged = 0
    for sp in (base_spec, compare_spec):
        text = sp.read_text(encoding="utf-8")
        for sec in extract_sections(text):
            total += 1
            if is_boilerplate_section(sec.get("title", ""), sec.get("content", "")[:500], cfg):
                flagged += 1
    rate = (flagged / total) if total else 0
    print(f"\n[测量] 章节总数={total}, boilerplate 命中={flagged}, 占比={rate:.1%}")
    assert flagged > 0  # 至少能检测到一个 boilerplate 章节
