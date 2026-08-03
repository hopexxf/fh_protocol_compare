"""
Boilerplate 过滤模块（思路 1）

在 diff 之后、LLM 分析之前，过滤与业务功能无关的章节
（版权 / 目录 / 参考文献 / 索引 / 术语表 / 修订历史 / 前言等）。

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


# 标题黑名单（小写子串匹配，双语）
BOILERPLATE_TITLES = {
    "contents", "目录",
    "revision history", "修订历史",
    "document history",
    "foreword", "前言",
    "list of tables", "表格列表",
    "list of figures", "图列表",
    "references", "参考文献",
    "index", "索引",
    "glossary", "术语",
    "abbreviations", "缩写",
    "notice",
    "copyright", "版权",
}

# 内容信号（极强 boilerplate 指示，保守使用，避免误删功能章节）
BOILERPLATE_CONTENT = {
    "©",
    "all rights reserved",
    "re-published",
    "confidential",
}


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
    if t and any(kw in t for kw in BOILERPLATE_TITLES):
        return True

    # 内容信号
    if c and any(sig in c for sig in BOILERPLATE_CONTENT):
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

    # 标题 / 内容 boilerplate 判定
    if cfg.get("skip_boilerplate", True):
        base_bp = is_boilerplate_section(bt, bc, cfg)
        compare_bp = is_boilerplate_section(ct, cc, cfg)
        if bt and ct:
            # 对齐对：任一侧命中即丢弃
            if base_bp or compare_bp:
                return True
        elif not ct:
            # base_only
            if base_bp:
                return True
        elif not bt:
            # compare_only
            if compare_bp:
                return True

    # page 级兜底（封面 / 末尾页）
    fp = cfg.get("skip_front_pages", 0) or 0
    bp = cfg.get("skip_back_pages", 0) or 0
    if fp > 0 or bp > 0:
        pages = [
            p for p in (_page_int(item.get("base_page")), _page_int(item.get("compare_page")))
            if p
        ]
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

    # 计算全局最大页码（供 skip_back_pages 使用）
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

    cfg = cfg or {}

    def _keep(section: dict) -> bool:
        return not is_boilerplate_section(
            section.get("title", "") or "",
            section.get("content", "") or "",
            cfg,
        )

    # 过滤对齐对：任一侧为 boilerplate 则丢弃整对
    new_alignments = [
        a for a in alignment.get("alignments", [])
        if _keep(a.get("base_section", {}))
        and _keep(a.get("compare_section", {}))
    ]

    # 过滤 base_only / compare_only
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
    total = 0
    title_hits = 0
    content_hits = 0
    for item in diff_raw:
        total += 1
        bt = item.get("base_section_title", "") or ""
        ct = item.get("compare_section_title", "") or ""
        bc = item.get("base_content", "") or ""
        cc = item.get("compare_content", "") or ""
        if (bt and any(kw in _norm_title(bt) for kw in BOILERPLATE_TITLES)) or \
           (ct and any(kw in _norm_title(ct) for kw in BOILERPLATE_TITLES)):
            title_hits += 1
        if (bc and any(sig in bc.lower() for sig in BOILERPLATE_CONTENT)) or \
           (cc and any(sig in cc.lower() for sig in BOILERPLATE_CONTENT)):
            content_hits += 1
    union = len({
        i for i, item in enumerate(diff_raw)
        if (item.get("base_section_title", "") and any(kw in _norm_title(item["base_section_title"]).lower() for kw in BOILERPLATE_TITLES))
        or (item.get("compare_section_title", "") and any(kw in _norm_title(item["compare_section_title"]).lower() for kw in BOILERPLATE_TITLES))
        or (item.get("base_content", "") and any(sig in item["base_content"].lower() for sig in BOILERPLATE_CONTENT))
        or (item.get("compare_content", "") and any(sig in item["compare_content"].lower() for sig in BOILERPLATE_CONTENT))
    })
    return {"total": total, "title_hits": title_hits, "content_hits": content_hits, "union": union}
