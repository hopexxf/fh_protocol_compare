"""思路 2 相似聚类 — 单元测试 + 历史数据本地集成（零 LLM）"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cluster import cluster_diff_items, expand_analysis


def _item(text, base_title="", compare_title="", has_diff=True):
    return {
        "base_section_title": base_title,
        "compare_section_title": compare_title,
        "base_content": text,
        "compare_content": text,
        "diff_summary": text,
        "has_diff": has_diff,
    }


def test_cluster_disabled_passthrough():
    items = [_item("hello world"), _item("foo bar")]
    reps, cmap = cluster_diff_items(items, {"enabled": False})
    assert len(reps) == 2
    assert cmap == [[0], [1]]


def test_cluster_groups_near_duplicates():
    items = [
        _item("eCPRI header RAN port bitmap beamforming control section type 0 U-plane message"),
        _item("eCPRI header RAN port bitmap beamforming control section type 0 U-plane message modified"),
        _item("FFT size 4096 sub-carrier spacing 30 kHz uplink transform precoding"),
    ]
    cfg = {"enabled": True, "similarity_threshold": 0.5}
    reps, cmap = cluster_diff_items(items, cfg)
    assert len(reps) == 2  # 前两个合并
    flat = [m for cl in cmap for m in cl]
    assert sorted(flat) == [0, 1, 2]


def test_cluster_representative_is_most_complete():
    items = [
        _item("eCPRI header RAN port bitmap beamforming"),
        _item("eCPRI header RAN port bitmap beamforming with detailed mapping context"),
    ]
    reps, cmap = cluster_diff_items(items, {"enabled": True, "similarity_threshold": 0.5})
    assert reps[0]["base_content"] == items[1]["base_content"]


def test_cluster_separates_distinct():
    items = [
        _item("eCPRI header RAN port bitmap"),
        _item("compression method block floating point with 8 bit exponent"),
        _item("PRACH preamble format 0 with 839 sequence length"),
    ]
    reps, cmap = cluster_diff_items(items, {"enabled": True, "similarity_threshold": 0.5})
    assert len(reps) == 3  # 完全不同，不合并


def test_expand_analysis_backfills_members():
    items = [
        _item("eCPRI header RAN port bitmap beamforming control"),
        _item("eCPRI header RAN port bitmap beamforming control with detailed context"),
        _item("FFT size 4096 sub-carrier spacing 30 kHz"),
    ]
    cfg = {"enabled": True, "similarity_threshold": 0.5}
    reps, cmap = cluster_diff_items(items, cfg)
    analyzed_reps = [
        {**rep, "llm_result": {"diffs": [{"type": "x", "impact": "高"}], "summary": f"rep{i}"}}
        for i, rep in enumerate(reps)
    ]
    full = expand_analysis(analyzed_reps, cmap, items)
    assert len(full) == 3
    # 成员保留各自 metadata
    assert full[0]["base_content"] == items[0]["base_content"]
    assert full[1]["base_content"] == items[1]["base_content"]
    # 成员共享代表项分析块（前两项在同一簇，应共享 rep0）
    assert full[0]["llm_result"]["summary"] == full[1]["llm_result"]["summary"]


def test_integration_historical_data():
    """历史 diff_raw 本地聚类（零 LLM），断言代表项数 < 原始且 expand 还原全量。"""
    root = Path(__file__).resolve().parent.parent / "versions"
    matches = sorted(root.glob("20260801_*/diff_raw.json")) if root.exists() else []
    if not matches:
        pytest.skip("历史 diff_raw.json 不存在，跳过集成测试")

    diff_raw = json.loads(matches[0].read_text(encoding="utf-8"))
    cfg = {"enabled": True, "similarity_threshold": 0.88}
    reps, cmap = cluster_diff_items(diff_raw, cfg)
    assert len(reps) < len(diff_raw)  # 代表项应明显少于原始

    analyzed_reps = [
        {**rep, "llm_result": {"diffs": [{"type": "x", "impact": "高"}], "summary": "rep"}}
        for rep in reps
    ]
    full = expand_analysis(analyzed_reps, cmap, diff_raw)
    assert len(full) == len(diff_raw)  # expand 还原全量，顺序一致
    assert full[0]["base_section_title"] == diff_raw[0]["base_section_title"]  # 成员 metadata 完整
    print(f"\n[聚类] 原始 {len(diff_raw)} → 代表项 {len(reps)} (省 {len(diff_raw) - len(reps)} 次调用)")
