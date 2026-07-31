import sys
sys.path.insert(0, 'src')
import importlib
import analyzer
importlib.reload(analyzer)
from analyzer import _build_messages, _extract_json

DIFF_ITEM_ALIGNED = {
    'base_section_id': '1', 'base_section_number': '1', 'base_section_title': 'Test Base',
    'base_content': 'Base content here', 'compare_section_id': '2', 'compare_section_number': '2',
    'compare_section_title': 'Test Compare', 'compare_content': 'Compare content here',
    'diff_summary': 'diff summary', 'has_diff': True
}

msgs = _build_messages(DIFF_ITEM_ALIGNED)
print('Messages built OK:', len(msgs))

# Test 1: raw JSON without markdown
raw1 = '{"diffs": [{"type": "design-diff", "impact": "高", "base_quote": "U-plane", "compare_quote": "C-plane", "description": "变化了", "workload_hint": "需重新设计"}], "summary": "U-plane 变更为 C-plane"}'
ext1 = _extract_json(raw1)
print('Test1 (no markdown) extracted:', repr(ext1[:60]))
import json
try:
    obj1 = json.loads(ext1)
    print('Test1 parsed OK, keys:', list(obj1.keys()))
    print('Test1 has diffs+summary:', all(k in obj1 for k in ['diffs','summary']))
except Exception as e:
    print('Test1 parse error:', e)

# Test 2: markdown-wrapped JSON (```json ... ```)
raw2 = '''```json
{"diffs": [{"type": "design-diff", "impact": "高", "base_quote": "U-plane", "compare_quote": "C-plane", "description": "变化了", "workload_hint": "需重新设计"}], "summary": "U-plane 变更为 C-plane"}
```'''
ext2 = _extract_json(raw2)
print('Test2 (markdown) extracted:', repr(ext2[:60]))
try:
    obj2 = json.loads(ext2)
    print('Test2 parsed OK, keys:', list(obj2.keys()))
    print('Test2 has diffs+summary:', all(k in obj2 for k in ['diffs','summary']))
except Exception as e:
    print('Test2 parse error:', e)

# Test 3: markdown-wrapped JSON (```json ... ``` without closing ```)
raw3 = '''```json
{"diffs": [{"type": "design-diff", "impact": "高", "base_quote": "U-plane", "compare_quote": "C-plane", "description": "变化了", "workload_hint": "需重新设计"}], "summary": "U-plane 变更为 C-plane"}'''
ext3 = _extract_json(raw3)
print('Test3 (markdown no close) extracted:', repr(ext3[:60]))
try:
    obj3 = json.loads(ext3)
    print('Test3 parsed OK, keys:', list(obj3.keys()))
    print('Test3 has diffs+summary:', all(k in obj3 for k in ['diffs','summary']))
except Exception as e:
    print('Test3 parse error:', e)
