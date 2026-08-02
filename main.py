"""
FH Protocol Compare — 主入口

用法：
  # 单次比对
  py -3 main.py --base input/base/spec_a.pdf --compare input/compare/spec_b.pdf

  # 批量比对（config/settings.yml 中配置 Base 和 Compare 列表）
  py -3 main.py --batch

  # 完整参数
  py -3 main.py ^
    --base input/base/spec_a.pdf ^
    --compare input/compare/spec_b.pdf ^
    --output versions ^
    --no-archive
"""

import argparse
import logging
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
from src.reporter import generate_report, save_artifacts


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
    执行单次比对，返回报告 Markdown。
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
    logger.info("[1/5] 解析文档...")
    base_md, _, _ = parse_document(str(base_path))
    compare_md, _, _ = parse_document(str(compare_path))
    logger.info(f"[1/5] 解析完成：Base {len(base_md)} chars，Compare {len(compare_md)} chars")

    # ---- Step 2: 章节对齐 ----
    logger.info("[2/5] 章节对齐...")
    alignment = align_markdown(base_md, compare_md)
    logger.info(f"[2/5] 对齐完成：{len(alignment['alignments'])} 对，"
                f"Base 独有 {len(alignment['base_only'])} 节，"
                f"Compare 独有 {len(alignment['compare_only'])} 节")

    # ---- Step 3: 差异提取 ----
    logger.info("[3/5] 提取文本差异...")
    diff_raw = diff_aligned_sections(base_md, compare_md, alignment)
    diffs_found = sum(1 for d in diff_raw if d.get("has_diff"))
    logger.info(f"[3/5] 差异提取完成：{diffs_found} 个章节存在差异")

    # 子集模式：仅分析前 N 个差异条目（用于快速验证）
    if max_items and max_items > 0:
        diff_raw = diff_raw[:max_items]
        logger.info(f"[3/5] 子集模式：截取前 {len(diff_raw)} 个差异条目（--max-items={max_items}）")

    # ---- Step 4: LLM 分析 ----
    logger.info(f"[4/5] LLM 语义分析（concurrency={concurrency}）...")
    analyzed = analyze_diff_batch(diff_raw, concurrency=concurrency)
    stats = summarize_all(analyzed)
    logger.info(f"[4/5] 分析完成：共 {stats['total_diff_items']} 个差异条目，"
                f"高影响 {stats['by_impact'].get('高', 0)}，"
                f"中影响 {stats['by_impact'].get('中', 0)}，"
                f"低影响 {stats['by_impact'].get('低', 0)}")

    # ---- Step 5: 生成报告 ----
    logger.info("[5/5] 生成报告...")
    report_md = generate_report(base_name, compare_name, analyzed, stats)

    # 归档
    if archive:
        save_artifacts(
            output_dir=str(output_dir),
            base_name=base_name,
            compare_name=compare_name,
            base_md=base_md,
            compare_md=compare_md,
            alignment=alignment,
            diff_raw=diff_raw,
            analyzed=analyzed,
            stats=stats,
            report_md=report_md,
        )
        logger.info(f"[完成] 产物归档至: {output_dir}")
    else:
        # 直接输出到当前目录
        report_file = Path.cwd() / f"report_{Path(base_name).stem}_{Path(compare_name).stem}.md"
        report_file.write_text(report_md, encoding="utf-8")
        logger.info(f"[完成] 报告已写入: {report_file}")

    return report_md


def run_batch(config: dict, logger: logging.Logger) -> None:
    """批量执行多 Compare 比对"""
    base_file = config.get("batch", {}).get("base_file", "")
    compare_files: list = config.get("batch", {}).get("compare_files", [])
    output_dir = config.get("paths", {}).get("output_dir", "versions")

    if not base_file:
        logger.error("批量模式：config/settings.yml 中未配置 batch.base_file")
        return
    if not compare_files:
        logger.error("批量模式：config/settings.yml 中未配置 batch.compare_files")
        return

    base_path = Path(base_file)
    if not base_path.is_absolute():
        base_path = Path(__file__).resolve().parent / base_file

    logger.info(f"[批量] Base={base_path}，共 {len(compare_files)} 个 Compare")
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
    parser = argparse.ArgumentParser(description="FH Protocol Compare — 5G NR 前传协议文档比对工具")
    parser.add_argument("--base", help="Base 文档路径（PDF 或 DOCX）")
    parser.add_argument("--compare", help="Compare 文档路径（PDF 或 DOCX）")
    parser.add_argument("--batch", action="store_true", help="从 config/settings.yml 读取批量比对配置")
    parser.add_argument("--output", default="versions", help="输出目录（默认: versions）")
    parser.add_argument("--no-archive", action="store_true", help="不归档产物，仅输出报告")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细日志")
    parser.add_argument("--max-items", type=int, default=None, help="子集模式：仅分析前 N 个差异条目")
    parser.add_argument("--concurrency", type=int, default=1, help="LLM 并发数（默认 1，Gateway 并发易挂死）")
    args = parser.parse_args()

    # 日志
    logger = setup_logging()
    if args.verbose:
        for h in logger.handlers:
            h.setLevel(logging.DEBUG)

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
        print("\n示例：")
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
