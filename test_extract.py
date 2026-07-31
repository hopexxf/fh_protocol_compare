import sys, json
sys.path.insert(0, 'src')
from analyzer import _extract_json, _find_json_end

# Test _find_json_end with nested objects
text = '{"diffs": [{"type": "design-diff", "impact": "高"}], "summary": "test"}'
end = _find_json_end(text)
print("end pos:", end)
print("matched:", repr(text[:end+1]))

# Test with markdown-wrapped JSON (no closing ``` after the JSON block)
raw = '{"diffs": [{"type": "design-diff", "impact": "高", "base_quote": "U-plane", "compare_quote": "C-plane", "description": "U-plane 变更为 C-plane", "workload_hint": "需重新设计"}], "summary": "U-plane 变更为 C-plane，影响较高"}'
extracted = _extract_json(raw)
print("Extracted:", repr(extracted[:80]))
try:
    obj = json.loads(extracted)
    print("Parsed OK:", list(obj.keys()))
except Exception as e:
    print("Parse error:", e)
