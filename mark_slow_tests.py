"""标记 test_parser.py 中的慢测试"""
import re

with open('tests/test_parser.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 在所有 @pytest.mark.skipif 前添加 @pytest.mark.slow
# 但排除错误处理测试（不涉及 PDF 解析）
lines = content.split('\n')
output = []
i = 0

while i < len(lines):
    line = lines[i]

    # 检测 skipif 标记
    if '@pytest.mark.skipif' in line:
        # 检查是否是错误处理测试（不标记为 slow）
        # 向后查找函数名
        j = i + 1
        while j < len(lines) and 'def test_' not in lines[j]:
            j += 1

        func_name = lines[j] if j < len(lines) else ''
        is_error_test = 'nonexistent' in func_name or 'ErrorHandling' in func_name or 'raises' in func_name

        if not is_error_test:
            output.append('    @pytest.mark.slow')

        output.append(line)
    else:
        output.append(line)

    i += 1

with open('tests/test_parser.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))

print("OK - 慢测试标记完成")
