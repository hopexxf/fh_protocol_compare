"""
FH Protocol Compare - 主入口

用法:
  # 单次比对
  py -3 main.py --base input/base/spec_a.pdf --compare input/compare/spec_b.pdf

  # 批量比对(config/settings.yml 中配置 Base 和 Compare 列表)
  py -3 main.py --batch

  # 完整参数
  py -3 main.py ^
    --base input/base/spec_a.pdf ^
    --compare input/compare/spec_b.pdf ^
    --output versions ^
    --no-archive
"""

import argparse
import re
import json
import logging
from typing import Optional
import os
import sys
from datetime import date
from pathlib import Path

# 添加 src 到 path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config_loader import get_config
from src.parser_pdf import parse_pdf
from src.parser_docx import parse_document
from src.aligner import align_markdown
from src.differ import diff_aligned_sections
from src.analyzer import analyze_diff_batch, summarize_all
from src.reporter import generate_report
from src.filter_boilerplate import filter_alignment, filter_diff_items
from src.cluster import cluster_diff_items, expand_analysis
from src.abstractor import generate_abstract


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def setup_logging(log_dir: str = "logs") -> logging.Logger:
    """配置结构化日志"""
    log_path = Path(__file__).resolve().parent / log_dir
    log_path.mkdir(exist_ok=True)
    log_file = log_path / f"fh_compare_{date.today().strftime('%Y-%m-%d')}.log"

    logger = logging.getLogger("fh_protocol_compare")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-8s | %(message)s"))
    logger.addHandler(console)

    file_h = logging.FileHandler(log_file, encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(file_h)

    return logger


# ---------------------------------------------------------------------------
# 核心流程
# ---------------------------------------------------------------------------

def _load_intermediate_artifacts(version_dir):
    """从已有版本目录加载中间产物，供 --resume 使用。

    返回 dict 含：base_md, compare_md, alignment, diff_raw, analyzed, stats, full_diff_raw。
    文件缺失时对应字段为 None。
    """
    def read_json(name):
        p = version_dir / name
        if p.exists():
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        return None

    def read_md(name):
        p = version_dir / name
        if p.exists():
            with open(p, encoding='utf-8') as f:
                return f.read()
        return ""

    return {
        "base_md":       read_md("base_spec.md"),
        "compare_md":    read_md("compare_spec.md"),
        "alignment":     read_json("alignment.json"),
        "diff_raw":      read_json("diff_raw.json"),
        "analyzed":      read_json("analyzed.json"),
        "stats":         read_json("stats.json"),
        "full_diff_raw": read_json("diff_raw_full.json"),
    }


def _save_intermediate_artifacts(
    output_dir: str,
    base_name: str,
    compare_name: str,
    base_md: str,
    compare_md: str,
    alignment: dict,
    diff_raw: list[dict],
    analyzed: list[dict],
    stats: dict,
    full_diff_raw: Optional[list] = None,
    *,  # logger follows as keyword-only
    logger: Optional[logging.Logger] = None,
) -> Path:
    """归档中间产物（base/compare/alignment/diff/analyzed/stats），返回版本目录路径。"""
    from datetime import date

    date_str = date.today().strftime("%Y%m%d")
    safe_base = Path(base_name).stem.replace(" ", "_")
    safe_compare = Path(compare_name).stem.replace(" ", "_")
    version_dir = Path(output_dir) / f"{date_str}_{safe_base}_vs_{safe_compare}"
    version_dir.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "base_spec.md": base_md,
        "compare_spec.md": compare_md,
        "alignment.json": json.dumps(alignment, indent=2, ensure_ascii=False),
        "diff_raw.json": json.dumps(diff_raw, indent=2, ensure_ascii=False),
        "analyzed.json": json.dumps(analyzed, indent=2, ensure_ascii=False),
        "stats.json": json.dumps(stats, indent=2, ensure_ascii=False),
    }
    if full_diff_raw:
        artifacts["diff_raw_full.json"] = json.dumps(full_diff_raw, indent=2, ensure_ascii=False)

    for fname, content in artifacts.items():
        path = version_dir / fname
        path.write_text(content, encoding="utf-8")
        if logger:
            logger.debug(f"[Reporter] 归档: {path}")

    if logger:
        logger.info(f"[Reporter] 中间产物已归档至: {version_dir}")
    return version_dir


def run_comparison(
    base_path: str,
    compare_path: str,
    output_dir: str = "versions",
    archive: bool = True,
    logger: logging.Logger = None,
    max_items: int = None,
    concurrency: int = 1,
) -> str:
    """
    执行单次比对,返回报告 Markdown。
    """
    if logger is None:
        logger = logging.getLogger("fh_protocol_compare")

    base_path = Path(base_path).resolve()
    compare_path = Path(compare_path).resolve()
    output_dir = Path(output_dir)

    if not base_path.exists():
        raise FileNotFoundError(f"Base 文件不存在: {base_path}")
    if not compare_path.exists():
        raise FileNotFoundError(f"Compare 文件不存在: {compare_path}")

    base_name = base_path.name
    compare_name = compare_path.name
    logger.info(f"[比对] Base={base_name}  Compare={compare_name}")

    # ---- Step 1: 解析文档 ----
    logger.info("[1/6] 解析文档...")
    base_md, _, _ = parse_document(str(base_path))
    compare_md, _, _ = parse_document(str(compare_path))
    logger.info(f"[1/6] 解析完成:Base {len(base_md)} chars,Compare {len(compare_md)} chars")

    # ---- Step 2: 章节对齐 ----
    logger.info("[2/6] 章节对齐...")
    alignment = align_markdown(base_md, compare_md)
    logger.info(f"[2/6] 对齐完成:{len(alignment['alignments'])} 对,"
                f"Base 独有 {len(alignment['base_only'])} 节,"
                f"Compare 独有 {len(alignment['compare_only'])} 节")

    # ---- Step 2.5: Boilerplate 过滤(思路 1,align 后 / diff 前) ----
    filter_cfg = get_config().get("filter", {})
    if filter_cfg.get("enabled", False):
        alignment = filter_alignment(alignment, filter_cfg)

    # ---- Step 3: 差异提取 ----
    logger.info("[3/6] 提取文本差异...")
    diff_raw = diff_aligned_sections(base_md, compare_md, alignment)
    diffs_found = sum(1 for d in diff_raw if d.get("has_diff"))
    logger.info(f"[3/6] 差异提取完成:{diffs_found} 个章节存在差异")

    # 保存完整版(用于归档;子集模式截断在分析之后,不丢失完整数据)
    full_diff_raw = list(diff_raw)

    # 子集模式:仅分析前 N 个差异条目(用于快速验证)
    if max_items and max_items > 0:
        diff_raw = diff_raw[:max_items]
        logger.info(f"[3/6] 子集模式：截取前 {len(diff_raw)} 个差异条目（--max-items={max_items})")

    # ---- Step 3.5: Boilerplate 过滤（diff 后 / LLM 前） ----
    filter_cfg = get_config().get("filter", {})
    # 过滤默认开启（除非显式配置 enabled: false）
    if filter_cfg.get("enabled", True):
        before = len(diff_raw)
        diff_raw = filter_diff_items(diff_raw, filter_cfg)
        after = len(diff_raw)
        logger.info(f"[3.5/6] Boilerplate 过滤：{before} → {after} 条（过滤 {before - after} 条）")

    # ---- Step 4: LLM 分析 ----
    logger.info(f"[4/6] LLM 语义分析(concurrency={concurrency})...")
    cluster_cfg = get_config().get("cluster", {})
    if cluster_cfg.get("enabled", False):
        rep_items, cluster_map = cluster_diff_items(diff_raw, cluster_cfg)
        logger.info(f"[4/6] 聚类:{len(diff_raw)} → {len(rep_items)} 代表项")
        analyzed_reps = analyze_diff_batch(rep_items, concurrency=concurrency)
        analyzed = expand_analysis(analyzed_reps, cluster_map, diff_raw)
    else:
        analyzed = analyze_diff_batch(diff_raw, concurrency=concurrency)
    stats = summarize_all(analyzed)
    logger.info(f"[4/6] 分析完成:共 {stats['total_diff_items']} 个差异条目,"
                f"高影响 {stats['by_impact'].get('高', 0)},"
                f"中影响 {stats['by_impact'].get('中', 0)},"
                f"低影响 {stats['by_impact'].get('低', 0)}")

    # ---- Step 4.5: 立即归档中间产物（无论后续是否成功） ----
    if archive:
        logger.info("[4.5/6] 归档中间产物（base/compare/alignment/diff/analyzed/stats）...")
        try:
            _save_intermediate_artifacts(
                output_dir=str(output_dir),
                base_name=base_name,
                compare_name=compare_name,
                base_md=base_md,
                compare_md=compare_md,
                alignment=alignment,
                diff_raw=diff_raw,
                full_diff_raw=full_diff_raw,
                analyzed=analyzed,
                stats=stats,
                logger=logger,
            )
        except Exception as e:
            logger.warning(f"[4.5/6] 中间产物归档失败: {e}")

    # ---- Step 5: 生成报告 ----
    logger.info("[5/6] 生成报告...")
    report_md = generate_report(base_name, compare_name, analyzed, stats)

    # ---- Step 6: 摘要生成(思路 3) ----
    abstract_md = None
    abstract_cfg = get_config().get("abstract", {})
    if abstract_cfg.get("enabled", False):
        logger.info("[6/6] 生成摘要...")
        try:
            abstract_md = generate_abstract(analyzed, abstract_cfg)
        except Exception as e:
            logger.warning(f"[6/6] 摘要生成失败: {e}，继续归档其他产物")
            abstract_md = None

    # 归档报告和摘要（中间产物已在 Step 4.5 归档）
    if archive:
        # 找到版本目录
        from datetime import date
        date_str = date.today().strftime("%Y%m%d")
        safe_base = Path(base_name).stem.replace(" ", "_")
        safe_compare = Path(compare_name).stem.replace(" ", "_")
        version_dir = Path(output_dir) / f"{date_str}_{safe_base}_vs_{safe_compare}"

        # 只保存报告和摘要（追加）
        report_path = version_dir / "report.md"
        report_path.write_text(report_md, encoding="utf-8")
        logger.debug(f"[Reporter] 归档: {report_path}")

        if abstract_md:
            abs_path = version_dir / "report_abstract.md"
            abs_path.write_text(abstract_md, encoding="utf-8")
            logger.debug(f"[Reporter] 归档: {abs_path}")

        logger.info(f"[完成] 产物归档至: {version_dir}")
    else:
        # 直接输出到当前目录
        report_file = Path.cwd() / f"report_{Path(base_name).stem}_{Path(compare_name).stem}.md"
        report_file.write_text(report_md, encoding="utf-8")
        logger.info(f"[完成] 报告已写入: {report_file}")
        if abstract_md:
            abs_file = Path.cwd() / f"report_abstract_{Path(base_name).stem}_{Path(compare_name).stem}.md"
            abs_file.write_text(abstract_md, encoding="utf-8")
            logger.info(f"[完成] 摘要已写入: {abs_file}")

    return report_md


def run_batch(config: dict, logger: logging.Logger) -> None:
    """批量执行多 Compare 比对"""
    base_file = config.get("batch", {}).get("base_file", "")
    compare_files: list = config.get("batch", {}).get("compare_files", [])
    output_dir = config.get("paths", {}).get("output_dir", "versions")

    if not base_file:
        logger.error("批量模式:config/settings.yml 中未配置 batch.base_file")
        return
    if not compare_files:
        logger.error("批量模式:config/settings.yml 中未配置 batch.compare_files")
        return

    base_path = Path(base_file)
    if not base_path.is_absolute():
        base_path = Path(__file__).resolve().parent / base_file

    logger.info(f"[批量] Base={base_path},共 {len(compare_files)} 个 Compare")
    for i, cf in enumerate(compare_files):
        cmp_path = Path(cf)
        if not cmp_path.is_absolute():
            cmp_path = Path(__file__).resolve().parent / cf
        logger.info(f"[批量] ({i+1}/{len(compare_files)}) 比对中: {cmp_path.name}")
        try:
            run_comparison(str(base_path), str(cmp_path), output_dir=output_dir, logger=logger)
        except Exception as e:
            logger.error(f"[批量] 比对失败 ({cmp_path.name}): {e}")
            continue


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="FH Protocol Compare - 5G NR 前传协议文档比对工具")
    parser.add_argument("--base", help="Base 文档路径(PDF 或 DOCX)")
    parser.add_argument("--compare", help="Compare 文档路径(PDF 或 DOCX)")
    parser.add_argument("--batch", action="store_true", help="从 config/settings.yml 读取批量比对配置")
    parser.add_argument("--output", default="versions", help="输出目录(默认: versions)")
    parser.add_argument("--no-archive", action="store_true", help="不归档产物,仅输出报告")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细日志")
    parser.add_argument("--max-items", type=int, default=None, help="子集模式:仅分析前 N 个差异条目")
    parser.add_argument("--concurrency", type=int, default=1, help="LLM 并发数(默认 1,Gateway 并发易挂死)")
    parser.add_argument("--resume", metavar="DIR", default=None,
                        help="从已有版本目录恢复：加载中间产物，跳过 Step 1-4，直接生成报告。"
                             "用于断网重跑或仅修改报告模板后重新生成。")
    args = parser.parse_args()

    # 日志
    logger = setup_logging()
    if args.verbose:
        for h in logger.handlers:
            h.setLevel(logging.DEBUG)

    if args.resume:
        resume_dir = Path(args.resume).resolve()
        if not resume_dir.exists():
            logger.error(f"--resume 目录不存在: {resume_dir}")
            sys.exit(1)
        logger.info(f"[Resume] 从 {resume_dir} 加载中间产物...")
        artifacts = _load_intermediate_artifacts(resume_dir)
        base_md       = artifacts["base_md"]       or ""
        compare_md    = artifacts["compare_md"]    or ""
        alignment     = artifacts["alignment"]     or {}
        diff_raw      = artifacts["diff_raw"]      or []
        analyzed      = artifacts["analyzed"]      or []
        stats         = artifacts["stats"]         or {}
        full_diff_raw = artifacts["full_diff_raw"] or None

        # 从目录名解析 base_name / compare_name（目录格式：{date}_{safe_base}_vs_{safe_compare}）
        name_part = resume_dir.name
        m_name = re.match(r"\d{8}_(.+?)_vs_(.+)$", name_part)
        if m_name:
            base_name    = m_name.group(1).replace("_", " ")
            compare_name = m_name.group(2).replace("_", " ")
        else:
            base_name, compare_name = "Base", "Compare"

        logger.info(f"[Resume] 加载 analyzed={len(analyzed)} items, stats={stats}")

        # Step 4.5: 重新归档（以当前时间为准，覆盖旧时间戳）
        version_dir = None
        if not args.no_archive:
            version_dir = _save_intermediate_artifacts(
                output_dir=args.output,
                base_name=base_name,
                compare_name=compare_name,
                base_md=base_md,
                compare_md=compare_md,
                alignment=alignment,
                diff_raw=diff_raw,
                analyzed=analyzed,
                stats=stats,
                full_diff_raw=full_diff_raw,
                logger=logger,
            )
            logger.info(f"[Resume] 产物已更新归档至: {version_dir}")

        # Step 5: 生成报告
        logger.info("[5/6] 生成报告...")
        report_md = generate_report(base_name, compare_name, analyzed, stats)

        # Step 6: 摘要
        abstract_md = None
        abstract_cfg = get_config().get("abstract", {})
        if abstract_cfg.get("enabled", False):
            logger.info("[6/6] 生成摘要...")
            try:
                abstract_md = generate_abstract(analyzed, abstract_cfg)
            except Exception as e:
                logger.warning(f"[6/6] 摘要生成失败: {e}，继续归档")

        # 归档最终产物
        if not args.no_archive and version_dir:
            save_artifacts(
                version_dir=version_dir,
                base_name=base_name,
                compare_name=compare_name,
                base_md=base_md,
                compare_md=compare_md,
                alignment=alignment,
                analyzed=analyzed,
                stats=stats,
                report_md=report_md,
                abstract_md=abstract_md,
                logger=logger,
            )

        logger.info("=" * 50)
        logger.info("比对完成（Resume 模式）")
        logger.info("=" * 50)
        return

    logger.info("=" * 50)
    logger.info("FH Protocol Compare 启动")
    logger.info("=" * 50)

    # 配置
    try:
        cfg = get_config()
    except FileNotFoundError as e:
        logger.error(f"配置加载失败: {e}")
        sys.exit(1)

    # 批量模式
    if args.batch:
        config_data = cfg._data if hasattr(cfg, "_data") else {}
        run_batch(config_data, logger)
        return

    # 单次比对
    if not args.base or not args.compare:
        parser.print_help()
        print("\n示例:")
        print("  py -3 main.py --base input/base/spec_v1.pdf --compare input/compare/spec_v2.pdf")
        print("  py -3 main.py --batch")
        sys.exit(1)

    try:
        run_comparison(
            base_path=args.base,
            compare_path=args.compare,
            output_dir=args.output,
            archive=not args.no_archive,
            logger=logger,
            max_items=args.max_items,
            concurrency=args.concurrency,
        )
        logger.info("比对完成。")
    except Exception as e:
        logger.error(f"比对失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
