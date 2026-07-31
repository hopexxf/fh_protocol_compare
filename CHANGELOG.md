# CHANGELOG — FH Protocol Compare

## [Unreleased]

### 表格提取（2026-08-01）

- **Phase 0**：JRE 25 + camelot 2.0.0 安装完成
- **Phase 1**：`extract_tables_camelot()` 实现，支持 stream/lattice 模式
- **Phase 1**：`_table_to_markdown()` / `_tables_to_markdown()` 表格转 Markdown
- **Phase 1**：`parse_pdf()` 返回三元组 `(md, pages, tables)`
- **Phase 2**：`_parse_sections_from_markdown()` 解析章节边界
- **Phase 2**：`_associate_tables_with_sections()` 表格关联章节（页码 + 文本匹配）
- **Phase 2**：`_insert_tables_into_sections()` 表格插回章节末尾
- **Phase 3**：慢测试标记 `@pytest.mark.slow`，pytest.ini 注册 marker
- **Phase 3**：全量测试 133 passed, 13 deselected (3.60s)
- **配置**：`pdf.use_camelot` / `camelot_flavor` / `table_min_accuracy` / `fallback_to_pdfplumber`
- **依赖**：`requirements.txt` 新增 `camelot-py>=2.0.0`

### 工程框架

- 创建完整项目骨架（目录结构 + 核心模块）
- `src/config_loader.py` — YAML 配置加载，支持环境变量覆盖
- `src/llm_client.py` — LLM 多端点降级链（19000 → 28789 → API Key）
- `src/parser_pdf.py` — PDF 解析（pdfplumber 主 + pdfminer 备选）
- `src/parser_docx.py` — Word 解析（python-docx）
- `src/aligner.py` — 章节对齐（TF-IDF + 余弦相似度）
- `src/differ.py` — 差异提取（diff-match-patch + difflib 备选）
- `src/analyzer.py` — LLM 语义分析（分类 + 影响评估）
- `src/reporter.py` — Markdown 报告生成 + 产物归档
- `main.py` — 主入口（单次 / 批量模式）
- `config/settings.yml` — 配置文件
- `requirements.txt` — 依赖清单
- `vendor/diff_match_patch.py` — diff-match-patch 源码（内联）
