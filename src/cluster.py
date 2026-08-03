"""
相似聚类模块（思路 2）

在 diff 之后、LLM 分析之前，对 diff 条目做高阈值语义聚类：
  - 每簇仅让代表项（内容最完整者）调用一次 LLM；
  - 分析后回填到该簇所有成员，成员保留各自出处（章节 / 页码 / 原文）。

默认 config cluster.enabled=False。
复用 aligner 的 TF-IDF + 余弦思路（scikit-learn 已在依赖中）。
聚类基于 base+compare 文本内容相似度（此时尚无 diff 类型，故不依赖类型）。
"""

import logging
from typing import Optional

logger = logging.getLogger("fh_protocol_compare.cluster")


def _item_text(item: dict) -> str:
    """构建聚类用文本：base + compare 内容 + diff 摘要。"""
    parts = []
    if item.get("base_content"):
        parts.append(item.get("base_content", ""))
    if item.get("compare_content"):
        parts.append(item.get("compare_content", ""))
    if item.get("diff_summary"):
        parts.append(item.get("diff_summary", ""))
    return " ".join(parts)


def _union_find(n: int):
    """并查集：返回 (parent_list, find, union)。"""
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    return parent, find, union


def cluster_diff_items(diff_raw: list[dict], cfg: Optional[dict] = None) -> tuple:
    """
    高阈值语义聚类。

    Args:
        diff_raw: differ 输出的差异条目列表（含对齐对 / base_only / compare_only）。
        cfg: 配置 dict，需含 enabled / similarity_threshold。

    Returns:
        (rep_items, cluster_map)
        - rep_items: 代表项列表（进 LLM 的条目）
        - cluster_map: list，cluster_map[k] = [member_indices]，
          member_indices 为成员在原 diff_raw 中的索引列表。
    """
    if not cfg or not cfg.get("enabled", False):
        # 未启用：每个条目各自成簇，代表项即自身
        return diff_raw, [[i] for i in range(len(diff_raw))]

    threshold = float(cfg.get("similarity_threshold", 0.88))
    texts = [_item_text(it) for it in diff_raw]
    n = len(texts)
    if n == 0:
        return [], []

    # 空文本（无内容）各自成簇，不参与聚类
    nonempty = [i for i in range(n) if texts[i].strip()]
    empty_clusters = [[i] for i in range(n) if not texts[i].strip()]
    if not nonempty:
        return diff_raw, [[i] for i in range(n)]

    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    try:
        vec = TfidfVectorizer(
            lowercase=True,
            stop_words="english",
            ngram_range=(1, 2),
            max_features=5000,
        )
        X = vec.fit_transform([texts[i] for i in nonempty])
    except ValueError:
        # 所有文本为空或单一 token，无法向量化 → 各自成簇
        return diff_raw, [[i] for i in range(n)]

    sim = cosine_similarity(X)
    _, find, union = _union_find(len(nonempty))

    for a in range(len(nonempty)):
        for b in range(a + 1, len(nonempty)):
            if sim[a, b] >= threshold:
                union(a, b)

    # 收集簇（按原索引）
    clusters: dict = {}
    for idx, orig in enumerate(nonempty):
        root = find(idx)
        clusters.setdefault(root, []).append(orig)

    all_clusters = list(clusters.values()) + empty_clusters

    # 选代表项：内容最完整（base+compare 文本长度最大，空文本优先跳过）
    rep_items = []
    cluster_map = []
    for cl in all_clusters:
        rep_idx = max(cl, key=lambda i: len(texts[i]) if texts[i].strip() else -1)
        rep_items.append(diff_raw[rep_idx])
        cluster_map.append(cl)

    saved = n - len(rep_items)
    if saved > 0:
        logger.info(
            f"[Cluster] 聚类：{n} → {len(rep_items)} 代表项（省 {saved} 次 LLM 调用，"
            f"thr={threshold}）"
        )
    return rep_items, cluster_map


def expand_analysis(
    analyzed_reps: list[dict],
    cluster_map: list,
    diff_raw: list[dict],
) -> list[dict]:
    """
    将代表项分析回填到各成员。

    成员保留各自 diff_raw 元数据（出处：章节 / 页码 / 原文），
    仅分析块（llm_result / llm_added）来自代表项。

    Returns:
        list[dict]，长度 = len(diff_raw)，顺序与 diff_raw 一致。
    """
    full = [None] * len(diff_raw)
    for k, member_indices in enumerate(cluster_map):
        rep = analyzed_reps[k] if k < len(analyzed_reps) else None
        llm_result = rep.get("llm_result") if rep else None
        llm_added = rep.get("llm_added") if rep else None
        for mi in member_indices:
            member = dict(diff_raw[mi])
            if llm_result is not None:
                member["llm_result"] = llm_result
            if llm_added is not None:
                member["llm_added"] = llm_added
            full[mi] = member

    # 兜底：任何遗漏项用原条目填充（理论上不会发生）
    for i in range(len(diff_raw)):
        if full[i] is None:
            full[i] = dict(diff_raw[i])

    return full
