# CHANGELOG — FH Protocol Compare

## [Unreleased]

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
