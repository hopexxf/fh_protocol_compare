# CHANGELOG

## v1.0 (2026-08-01)

### 核心功能
- 文档解析：PDF（pdfplumber + pdfminer）+ Word（python-docx）
- 章节对齐：TF-IDF + 余弦相似度，阈值 0.3
- 差异提取：diff-match-patch + difflib 备选
- LLM 分析：动态知识注入，异步并发批量
- 报告生成：Markdown + 产物归档

### 表格提取
- camelot 集成：stream/lattice 模式
- 表格关联章节：页码 + 文本匹配
- Markdown 输出：含页码溯源

### 配置
- LLM 多端点降级链（19000 → 61791 → AWS）
- 业务知识配置（21 个 diff_patterns）
- PDF 表格提取配置

### 测试
- 144 passed, 13 deselected (slow)
- pytest slow marker 注册

---

## v0.1 (2026-07-30)

### 工程框架
- 创建完整项目骨架
- 核心模块：config_loader, llm_client, parser_*, aligner, differ, analyzer, reporter
- 配置文件：settings.yml
- 测试套件：test_config, test_parser, test_aligner, test_differ, test_analyzer, test_reporter
- vendor/diff_match_patch.py 内联

---

## 提交记录

| 提交 | 日期 | 说明 |
|------|------|------|
| f23cf0f | 2026-08-01 | 清理临时调试脚本 |
| bea90a5 | 2026-08-01 | 表格提取方案完成状态更新 |
| f25ba90 | 2026-08-01 | Phase 3 集成测试完成 |
| e7f4d83 | 2026-08-01 | Phase 2 表格插回章节 |
| f4295c3 | 2026-08-01 | Phase 1 表格提取完成 |
| 0429eb6 | 2026-08-01 | 动态知识注入配置化 |
| e2a50eb | 2026-07-31 | P0 三项修复（页码、Base 独有、requirements）|
| d6667d7 | 2026-07-31 | Phase 6 报告生成模块 |
| 87ca6a0 | 2026-07-31 | Phase 5 LLM 分析模块 |
| 4d4d117 | 2026-07-31 | Phase 4 差异提取模块 |
| 82cc13e | 2026-07-31 | Phase 3 章节对齐模块 |
| 8645c02 | 2026-07-30 | Phase 2 文档解析模块 |
| 1ece0c8 | 2026-07-30 | Phase 1 配置加载模块 |
