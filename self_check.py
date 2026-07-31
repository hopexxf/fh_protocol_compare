"""自检脚本 — 工具执行前验证"""
import sys, importlib, subprocess

print("=" * 50)
print("FH Protocol Compare — 执行前自检")
print("=" * 50)

# 1. 模块导入
print("\n[1] 模块导入")
modules = [
    'src.config_loader',
    'src.parser_pdf',
    'src.parser_docx',
    'src.aligner',
    'src.differ',
    'src.analyzer',
    'src.reporter',
    'src.llm_client',
]
fails = []
for m in modules:
    try:
        importlib.import_module(m)
        print(f'  OK  {m}')
    except Exception as e:
        print(f'  FAIL {m}: {e}')
        fails.append(m)

# 2. 配置加载
print("\n[2] 配置加载")
if not fails:
    from src.config_loader import get_config
    cfg = get_config()
    print(f'  use_openclaw = {cfg.get("llm",{}).get("use_openclaw")}')
    print(f'  model = {cfg.get("llm",{}).get("model")}')
    print(f'  similarity_threshold = {cfg.get("alignment",{}).get("similarity_threshold")}')
    print(f'  min_change_chars = {cfg.get("diff",{}).get("min_change_chars")}')
    print(f'  log_dir = {cfg.get("paths",{}).get("log_dir")}')
else:
    print('  [SKIP — 模块导入失败]')

# 3. pytest 收集
print("\n[3] pytest 收集")
r = subprocess.run([sys.executable, '-m', 'pytest', 'tests/', '--collect-only', '-q'],
                   capture_output=True, text=True)
lines = [l for l in r.stdout.strip().splitlines() if 'test' in l.lower()]
print(f'  {lines[-1] if lines else "?"}')

# 4. 样本文件
print("\n[4] 样本文件")
import os
from pathlib import Path
base_dir = Path('input/base')
compare_dir = Path('input/compare')
for d, name in [(base_dir, 'Base'), (compare_dir, 'Compare')]:
    files = list(d.glob('*.pdf'))
    if files:
        for f in files:
            size = f.stat().st_size / 1024 / 1024
            print(f'  {name}: {f.name} ({size:.2f} MB)')
    else:
        print(f'  {name}: <空目录>')

# 5. vendor
print("\n[5] vendor diff_match_patch")
try:
    from vendor.diff_match_patch import diff_match_patch
    print("  OK  diff_match_patch 可导入")
except Exception as e:
    print(f"  FAIL: {e}")

# 6. 汇总
print("\n" + "=" * 50)
if fails:
    print(f"结果: FAILED ({len(fails)} 个模块导入失败)")
    sys.exit(1)
else:
    print("结果: ALL PASS")
    print("=" * 50)
