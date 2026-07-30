# FH Protocol Compare — 开发策划

> 状态：Phase 1-6 已完成 ✅
> 创建：2026-07-30
> 完成：2026-07-31

---

## 阶段概览

| 阶段 | 内容 | 产出 |
|------|------|------|
| **Phase 1** | 配置加载 + 框架集成测试 | `tests/test_config.py` |
| **Phase 2** | 文档解析 + PDF 测试 | `tests/test_parser.py` |
| **Phase 3** | 章节对齐 + TF-IDF 测试 | `tests/test_aligner.py` |
| **Phase 4** | 差异提取 + diff 测试 | `tests/test_differ.py` |
| **Phase 5** | LLM 分析 + Mock 测试 | `tests/test_analyzer.py` |
| **Phase 6** | 报告生成 + 端到端测试 | `tests/test_reporter.py` + `tests/test_differ.py` 等全部测试 |

---

## 测试样本说明

| 文件 | 路径 | 大小 | 用途 |
|------|------|------|------|
| O-RAN 规范 | `input/base/O-RAN.WG4.CUS.0-v05.00.pdf` | 8.3 MB | Base 大文档，含多章节 |
| ASTRI 架构设计 | `input/base/ASTRI_...v1.pdf` | 0.54 MB | 真实小文档（Base） |
| ASTRI 架构设计 | `input/compare/ASTRI_...v1.pdf` | 0.54 MB | 真实小文档（Compare，同内容测对齐） |

---

## Phase 1 — 配置加载

### 目标
验证 `config_loader.py` 正确读取 settings.yml，支持环境变量覆盖。

### 测试用例

**TC1-1：默认加载**
- 断言 `config.get("llm.model")` == `"gpt-3.5-turbo"`
- 断言 `config.get("paths.output_dir")` == `"versions"`
- 断言 `config.get("alignment.similarity_threshold")` == `0.3`

**TC1-2：环境变量覆盖**
- 设置环境变量 `LLM_MODEL=gpt-4`，reload
- 断言 `config.get("llm.model")` == `"gpt-4"`

**TC1-3：路径解析**
- 调用 `config.resolve_path("paths.output_dir")`
- 断言返回 Path 对象且存在

---

## Phase 2 — 文档解析

### 目标
验证 `parser_pdf.py` 和 `parser_docx.py` 能正确解析真实样本，输出结构化 Markdown。

### 测试用例

**TC2-1：pdfplumber 提取（小 PDF）**
- 输入：`input/compare/ASTRI_...v1.pdf`
- 断言：返回 pages list，长度 > 0
- 断言：每页含 `"page_num"` 和 `"text"`
- 断言：总字符数 > 1000

**TC2-2：pdfplumber 提取（大 PDF）**
- 输入：`input/base/O-RAN.WG4.CUS.0-v05.00.pdf`
- 断言：总页数 > 50
- 断言：无异常抛出

**TC2-3：Markdown 结构化输出**
- 输入：同 TC2-1
- 调用 `to_structured_markdown(pages)`
- 断言：输出包含 `# ` 标题行
- 断言：输出包含 `<!-- page=` 注释
- 断言：总长度 > 5000 字符

**TC2-4：pdfminer 回退**
- 模拟 pdfplumber 返回空文本场景
- 验证回退至 pdfminer（mock 或真实样本）

**TC2-5：自动识别解析器**
- 调用 `parse_document(path)`，分别传入 PDF 和 DOCX
- 断言：PDF 调用 pdfplumber，DOCX 调用 docx 解析器

**TC2-6：文件不存在**
- 断言 `parse_document("nonexistent.pdf")` 抛出 `FileNotFoundError`

---

## Phase 3 — 章节对齐

### 目标
验证 `aligner.py` 的标题提取和 TF-IDF 相似度匹配。

### 测试用例

**TC3-1：标题提取**
- 输入 Markdown（含 `# 1. Intro`、`## 1.1 Scope` 等）
- 调用 `extract_sections(md)`
- 断言：返回列表，元素含 `"id"`, `"level"`, `"number"`, `"title"`, `"content"`

**TC3-2：TF-IDF 相似度匹配（相同文档）**
- 输入：同一份 Markdown
- 调用 `align_sections(sections, sections)`
- 断言：所有章节均匹配自身，`similarity` >= 0.95

**TC3-3：语义对齐（O-RAN vs ASTRI 跨标准）**
- Base：O-RAN 样本（章节结构完整但编号与 ASTRI 不同）
- Compare：ASTRI 样本
- 断言：`alignments` 中含 U-plane 相关章节的匹配对
- 断言：`base_only` / `compare_only` 正确反映独有章节
- 输入：Base Markdown（O-RAN 样本前 5000 字符）和 Compare Markdown（同一份）
- 调用 `align_markdown(base_md, compare_md)`
- 断言：返回 dict 含 `"alignments"`, `"base_only"`, `"compare_only"`
- 断言：`alignments` 非空

**TC3-4：独有章节识别**
- Base 有 3 个章节，Compare 有 4 个（多了 1 个）
- 断言：compare_only 长度 == 1

**TC3-5：空文档处理**
- 输入空 Markdown
- 断言：不抛出异常，返回空对齐结果

**TC3-6：content 提取**
- 输入含多段落章节的 Markdown
- 断言：`sections[0]["content"]` 包含正文内容

---

## Phase 4 — 差异提取

### 目标
验证 `differ.py` 的文本级 diff 提取和变更检测。

### 测试用例

**TC4-1：diff-match-patch 加载**
- 断言：`from vendor.diff_match_patch import diff_match_patch` 成功
- 断言：`diff_text_dmp("hello", "hello world")` 返回包含 `insert` 类型的 diff

**TC4-2：相同文本**
- 调用 `diff_text_dmp("abc", "abc")`
- 断言：所有 diff 条目 type == `"equal"`

**TC4-3：变更文本**
- 调用 `diff_text_dmp("The quick brown fox", "The quick red fox")`
- 断言：结果含 `delete` 和 `insert` 条目

**TC4-4：显著变更判断**
- 调用 `has_significant_diff(diffs, threshold_chars=50)`（变更 > 50 字符）
- 断言返回 `True`

**TC4-5：噪声过滤**
- 调用 `has_significant_diff(diff, threshold_chars=200)`（变更 < 200 字符）
- 断言返回 `False`

**TC4-6：端到端章节 diff**
- 用 Phase 3 的 alignment 结果调用 `diff_aligned_sections()`
- 断言：返回列表，每个元素含 `"has_diff"`, `"base_section_id"`, `"compare_section_id"`

**TC4-7：独有章节 diff**
- alignment 含 base_only 和 compare_only
- 断言：diff 结果包含这两个条目的标记

---

## Phase 5 — LLM 分析

### 目标
验证 `analyzer.py` 调用 LLM（Mock），完成分类和影响评估。

### 测试用例

**TC5-1：Mock LLM 分析**
- Mock `llm_client.chat()` 返回预设 JSON
- 调用 `analyze_diff_item(diff_item)`
- 断言：返回结果含 `"llm_result"` 字段

**TC5-2：分类输出校验**
- Mock 返回：`{"diffs": [{"type": "feature-changed", "impact": "高", ...}], "summary": "xxx"}`
- 断言：`diffs[0]["type"]` == `"feature-changed"`
- 断言：`diffs[0]["impact"]` == `"高"`

**TC5-3：无显著差异跳过**
- diff_item `has_diff=False`
- 断言：LLM 未被调用，直接附加 `"llm_result": {"diffs": [], "summary": "无显著变更"}`

**TC5-4：批量分析**
- 5 个 diff_items 调用 `analyze_diff_batch()`
- 断言：返回 5 个结果
- 断言：Mock LLM 被调用 5 次

**TC5-5：统计汇总**
- 调用 `summarize_all(analyzed)`
- 断言：返回 dict 含 `"by_type"`, `"by_impact"`, `"total_diff_items"`

**TC5-6：JSON 解析失败降级**
- Mock LLM 返回非 JSON 文本
- 断言：不抛出异常，`llm_result.summary` 包含原始文本前 200 字符

---

## Phase 6 — 端到端集成测试

### 目标
用真实样本跑通全流程，生成第一份真实比对报告。

### 测试用例

**TC6-1：ASTRI vs ASTRI（自比对）**
- Base：`input/compare/ASTRI_...v1.pdf`（同文件）
- 执行：`run_comparison()`
- 断言：流程无异常，完成
- 断言：输出目录 `versions/*/` 存在

**TC6-2：产物完整性检查**
- 归档目录下存在：✓ base_spec.md ✓ compare_spec.md
  ✓ alignment.json ✓ diff_raw.json ✓ analyzed.json
  ✓ stats.json ✓ report.md

**TC6-3：alignment.json 格式**
- 读取归档的 `alignment.json`
- 断言：JSON 可解析，含 `"alignments"`, `"base_only"`, `"compare_only"`

**TC6-4：report.md 格式**
- 读取归档的 `report.md`
- 断言：包含 `# vs # — 协议差异比对报告`
- 断言：包含 `## 📊 统计概览` 和 `### 差异类型分布`

**TC6-5：O-RAN 大文档兼容性**
- Base：`input/base/O-RAN.WG4.CUS.0-v05.00.pdf`（8.3 MB）
- Compare：`input/compare/ASTRI_...v1.pdf`
- 执行完整流程（预计 3-5 分钟）
- 断言：无内存溢出，无超时崩溃，产物完整

**TC6-6：CLI 参数解析**
- 执行：`py -3 main.py --base input/base/ASTRI_...v1.pdf --compare input/compare/ASTRI_...v1.pdf --no-archive`
- 断言：输出包含 `[比对]` 日志
- 断言：产物输出到当前目录（非 versions/）

---

## 实施顺序

```
Phase 1（配置加载）
    ↓ pytest tests/test_config.py
Phase 2（文档解析）
    ↓ pytest tests/test_parser.py
Phase 3（章节对齐）
    ↓ pytest tests/test_aligner.py
Phase 4（差异提取）
    ↓ pytest tests/test_differ.py
Phase 5（LLM 分析）
    ↓ pytest tests/test_analyzer.py
Phase 6（集成测试）
    ↓ pytest tests/test_e2e.py
```

每阶段完成后汇报测试结果，全部通过后进入下一阶段。

---

## 风险提示

1. **大 PDF（8.3 MB）**：O-RAN 文档较大，pdfplumber 可能耗时较长；已设置超时回退
2. **LLM 依赖**：Phase 5 使用 Mock，不实际调用 LLM；Phase 6 才使用真实 LLM
3. **报告格式**：第一版 Markdown 模板待 Phase 6 真实输出后迭代优化
