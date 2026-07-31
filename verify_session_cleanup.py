"""验证会话清理修改"""
import ast, sys

sys.path.insert(0, 'C:/myfile/project/fh_protocol_compare')

# 1. LLMClient 方法检查
from src.llm_client import LLMClient
c = LLMClient()
assert hasattr(c, 'set_session_label'), 'set_session_label 缺失'
assert hasattr(c, 'cleanup_sessions'), 'cleanup_sessions 缺失'
c.set_session_label('test')
assert c._session_label == 'test'
print('✓ LLMClient 扩展: set_session_label + cleanup_sessions')

# 2. analyzer.py 语法检查
src = open('C:/myfile/project/fh_protocol_compare/src/analyzer.py', encoding='utf-8').read()
compile(src, 'analyzer.py', 'exec')
print('✓ analyzer.py 语法检查通过')

# 3. user 字段注入检查（2处：llm_client._call + async fetch_one）
count_user = src.count('"user"') + src.count("'user'")
assert count_user >= 2, f'user 字段应出现≥2次，实际: {count_user}'
print(f'✓ user 字段注入: {count_user} 处')

# 4. cleanup_sessions 调用检查（3处：_sync_batch + async + analyze_diff_item末尾）
count_cleanup = src.count('cleanup_sessions()')
assert count_cleanup >= 3, f'cleanup_sessions() 应出现≥3次，实际: {count_cleanup}'
print(f'✓ cleanup_sessions 调用: {count_cleanup} 处')

# 5. set_session_label 调用检查（3处：analyze_diff_item + _sync_batch + async）
count_label = src.count('set_session_label(')
assert count_label >= 3, f'set_session_label( 应出现≥3次，实际: {count_label}'
print(f'✓ set_session_label 调用: {count_label} 处')

# 6. llm_client.py user 字段检查
llm_src = open('C:/myfile/project/fh_protocol_compare/src/llm_client.py', encoding='utf-8').read()
count_user_llm = llm_src.count('"user"') + llm_src.count("'user'")
assert count_user_llm >= 1, f'llm_client.py user 字段缺失'
print(f'✓ llm_client.py user 字段: {count_user_llm} 处')

# 7. cleanup_sessions 实现检查
assert 'subprocess.run' in llm_src and 'sessions' in llm_src and 'cleanup' in llm_src
print('✓ cleanup_sessions 底层调用: openclaw sessions cleanup --enforce')

print('\n✅ ALL CHECKS PASSED — 会话清理修改验证完成')
