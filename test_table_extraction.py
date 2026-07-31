"""Phase 1 测试：验证表格提取功能"""

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

from src.parser_pdf import parse_pdf

# 测试 ASTRI PDF（27 页）
print("=" * 60)
print("测试 ASTRI PDF")
print("=" * 60)

md, pages, tables = parse_pdf('input/compare/ASTRI_NRBS_L1_v0.2.0_rc1_docs_PHY_Architecture_Design_ASTRI_0003191_NR_CRAN_RRU_Design_VS1.pdf')
print(f"页数: {len(pages)}")
print(f"表格数: {len(tables)}")
print(f"Markdown 长度: {len(md)}")

if tables:
    print("\n前 3 个表格:")
    for t in tables[:3]:
        print(f"  P{t['page_num']}: {len(t['table'])} 行, 准确度 {t.get('accuracy', 'N/A')}")

# 验证 Markdown 格式
if "| " in md and "---" in md:
    print("\nOK - 表格已转换为 Markdown 格式")
else:
    print("\nERROR - 表格未转换")

# 检查表格标记
if "<!-- table_page=" in md:
    print("OK - 表格包含页码标记")
else:
    print("ERROR - 表格缺少页码标记")
