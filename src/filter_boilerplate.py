"""
Boilerplate 过滤模块（思路 1）

在 diff 之后、LLM 分析之前，过滤与业务功能无关的章节
（版权 / 目录 / 参考文献 / 索引 / 术语表 / 修订历史 / 前言等）。

配置驱动：标题/内容黑名单从 config/boilerplate.yml 读取，支持运行时扩展。
- 双语高精度黑名单（标题子串匹配）。
- base_only / compare_only 同样过滤（它们正是 boilerplate 重灾区）。
- 可选 page 级兜底（跳过封面 / 末尾页）。
- 仅当 cfg 中 filter.enabled=True 时生效；filter.skip_boilerplate=False 时只做 page 级过滤。

目标：减少 LLM 调用 + 净化报告，聚焦业务功能对比。
"""

import logging
import re
from typing import Optional

logger = logging.getLogger("fh_protocol_compare.filter")

# =============================================================================
# 配置加载（懒加载，缓存在模块级别）
# =============================================================================

_BP_CONFIG: dict = {}
_TOC_PATTERNS_COMPILED: list = []


def _load_bp_config() -> dict:
    """懒加载 boilerplate.yml，只加载一次"""
    global _BP_CONFIG, _TOC_PATTERNS_COMPILED
    if _BP_CONFIG:
        return _BP_CONFIG
    try:
        from src.config_loader import get_boilerplate_config as _load

        _BP_CONFIG = _load()
    except Exception:
        _BP_CONFIG = {}
    # 预编译 TOC 正则
    raw_patterns = _BP_CONFIG.get("toc_patterns", [])
    _TOC_PATTERNS_COMPILED = [re.compile(p) for p in raw_patterns if isinstance(p, str)]
    return _BP_CONFIG


def _get_titles() -> set:
    return set(_load_bp_config().get("title_blacklist", []))


def _get_content_signals() -> set:
    return set(_load_bp_config().get("content_signals", []))


def _get_toc_signals() -> list:
    _load_bp_config()
    return _TOC_PATTERNS_COMPILED


# =============================================================================
# 公共 API
# =============================================================================


def _norm_title(title: str) -> str:
    return (title or "").strip().lower()


def _page_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_boilerplate_section(title: str, content: str, cfg: Optional[dict] = None) -> bool:
    """
    判断单个章节（单侧 base 或 compare）是否为 boilerplate。

    title/content 至少其一非空即可触发判断。
    cfg 可追加自定义关键词（boilerplate_keywords）。
    """
    cfg = cfg or {}
    t = _norm_title(title)
    c = (content or "").lower()

    # 标题黑名单
    if t and any(kw in t for kw in _get_titles()):
        return True

    # 内容信号
    if c and any(sig in c for sig in _get_content_signals()):
        return True

    # TOC 行残留信号
    if c and any(sig.search(c) for sig in _get_toc_signals()):
        return True

    # 自定义关键词（配置追加）
    extra = cfg.get("boilerplate_keywords") or []
    if extra and any(str(kw).lower() in (t + " " + c) for kw in extra):
        return True

    return False


def _should_drop(item: dict, cfg: dict, global_max_page: int) -> bool:
    """判断单个 diff_raw 条目是否应丢弃（boilerplate 或 page 范围）。"""
    bt = item.get("base_section_title", "") or ""
    ct = item.get("compare_section_title", "") or ""
    bc = item.get("base_content", "") or ""
    cc = item.get("compare_content", "") or ""

    if cfg.get("skip_boilerplate", True):
        base_bp = is_boilerplate_section(bt, bc, cfg)
        compare_bp = is_boilerplate_section(ct, cc, cfg)
        if bt and ct:
            if base_bp or compare_bp:
                return True
        elif not ct:
            if base_bp:
                return True
        elif not bt:
            if compare_bp:
                return True

    fp = cfg.get("skip_front_pages", 0) or 0
    bp = cfg.get("skip_back_pages", 0) or 0
    if fp > 0 or bp > 0:
        pages = [p for p in (_page_int(item.get("base_page")), _page_int(item.get("compare_page"))) if p]
        if pages:
            if fp > 0 and any(p <= fp for p in pages):
                return True
            if bp > 0 and global_max_page > 0 and any(p >= global_max_page - bp + 1 for p in pages):
                return True

    return False


def filter_diff_items(diff_raw: list[dict], cfg: Optional[dict] = None) -> list[dict]:
    """
    过滤 diff_raw 列表中的 boilerplate 条目。

    返回过滤后的新列表（不修改原列表）。
    仅当 cfg.get("enabled") 为 True 时生效，否则原样返回。
    """
    if not cfg or not cfg.get("enabled", False):
        return diff_raw

    global_max_page = 0
    for item in diff_raw:
        for pv in (item.get("base_page"), item.get("compare_page")):
            pi = _page_int(pv)
            if pi and pi > global_max_page:
                global_max_page = pi

    filtered = []
    dropped = 0
    for item in diff_raw:
        if _should_drop(item, cfg, global_max_page):
            dropped += 1
            continue
        filtered.append(item)

    if dropped:
        logger.info(
            f"[Filter] 过滤 boilerplate 章节 {dropped} 条，剩余 {len(filtered)} 条待分析"
        )
    return filtered


def filter_alignment(alignment: dict, cfg: Optional[dict] = None) -> dict:
    """
    在 align 之后、diff 之前过滤 boilerplate 章节。

    对对齐结构直接过滤（alignments 对齐对 + base_only + compare_only），
    返回新的对齐结构（不修改原结构）。

    cfg.get("enabled") 为 False 时原样返回。
    """
    if not cfg or not cfg.get("enabled", False):
        return alignment

    def _keep(section: dict) -> bool:
        return not is_boilerplate_section(
            section.get("title", "") or "",
            section.get("content", "") or "",
            cfg,
        )

    new_alignments = [
        a for a in alignment.get("alignments", [])
        if _keep(a.get("base_section", {})) and _keep(a.get("compare_section", {}))
    ]

    new_base_only = [s for s in alignment.get("base_only", []) if _keep(s)]
    new_compare_only = [s for s in alignment.get("compare_only", []) if _keep(s)]

    dropped_align = len(alignment.get("alignments", [])) - len(new_alignments)
    dropped_base = len(alignment.get("base_only", [])) - len(new_base_only)
    dropped_compare = len(alignment.get("compare_only", [])) - len(new_compare_only)
    total_dropped = dropped_align + dropped_base + dropped_compare

    if total_dropped:
        logger.info(
            f"[Filter] 过滤 boilerplate：对齐对 {dropped_align}，"
            f"Base 独有 {dropped_base}，Compare 独有 {dropped_compare}，"
            f"剩余 {len(new_alignments)} 对 + {len(new_base_only)} + {len(new_compare_only)}"
        )

    return {
        "alignments": new_alignments,
        "base_only": new_base_only,
        "compare_only": new_compare_only,
    }


def count_boilerplate(diff_raw: list[dict], cfg: Optional[dict] = None) -> dict:
    """
    统计 boilerplate 命中情况（本地测量用，无副作用）。
    返回 {total, title_hits, content_hits, union}。
    """
    cfg = cfg or {"enabled": True, "skip_boilerplate": True}
    titles = _get_titles()
    content_sigs = _get_content_signals()
    total = 0
    title_hits = 0
    content_hits = 0
    hit_set = set()
    for i, item in enumerate(diff_raw):
        total += 1
        bt = item.get("base_section_title", "") or ""
        ct = item.get("compare_section_title", "") or ""
        bc = item.get("base_content", "") or ""
        cc = item.get("compare_content", "") or ""
        bt_n = _norm_title(bt)
        ct_n = _norm_title(ct)
        t_hit = bool(bt_n and any(kw in bt_n for kw in titles)) or \
                bool(ct_n and any(kw in ct_n for kw in titles))
        c_hit = bool(bc and any(sig in bc.lower() for sig in content_sigs)) or \
                bool(cc and any(sig in cc.lower() for sig in content_sigs))
        if t_hit:
            title_hits += 1
        if c_hit:
            content_hits += 1
        if t_hit or c_hit:
            hit_set.add(i)
    return {"total": total, "title_hits": title_hits, "content_hits": content_hits, "union": len(hit_set)}
