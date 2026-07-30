# FH Protocol Compare — 技术方案

> 状态：v1.0（工程框架完成）
> 创建：2026-07-30
> 更新：2026-07-30（框架完成 + 环境验证）
> 负责人：通信大佬 + 通信小粉

---

## 一、目标

对比多份 5G NR 前传协议文档，以某一指定版本为 Base，分析另一个版本的**功能差异、设计差异、参数差异**，输出带原文溯源的 Markdown 报告，用于指导开发工作量评估。

---

## 二、输入输出

### 2.1 输入

| 文档 | 说明 |
|------|------|
| Base 文档 | 基准版本，用户指定，可为 PDF 或 Word |
| Compare 文档 | 待比对版本，可为 PDF 或 Word，支持多个 |

- 文档语言：英文为主（未来可能混入中文）
- 文档规模：PDF ~27 页，Word ~40 页（单份）

### 2.2 输出

- 每对 Base + Compare 生成一份独立报告：`report_{base}_{compare}_{date}.md`
- 产物归档到 `versions/{yyyymmdd}_{base_name}_vs_{compare_name}/`
- 报告正文以**中文**为主，关键原文引用**英文**，双语呈现

---

## 三、比对维度体系

| 维度 | 英文标签 | 说明 |
|------|---------|------|
| 功能新增 | `feature-added` | Compare 中有、Base 中无的功能 |
| 功能变更 | `feature-changed` | 两者均有但描述/范围变化 |
| 功能删除 | `feature-removed` | Base 中有、Compare 中无的功能 |
| 设计差异 | `design-diff` | 消息结构、状态机、流程步骤变化 |
| 参数差异 | `param-diff` | 字段定义、阈值、常量值变化 |
| 一致性问题 | `consistency-issue` | 单个文档内部描述不自洽 |

---

## 四、报告格式规范

每个差异点的输出格式：

```markdown
### D-XXX：{差异标题}

**类型**：{维度标签}
**影响**：高/中/低（需标注实现成本影响）
**位置**：
  - Base：{文档名}，第 {章}.{节} 节，P{页码}，段 {段落号}
  - Compare：{文档名}，第 {章}.{节} 节，P{页码}，段 {段落号}

**Base 原文**：
> {原文引用}

**Compare 原文**：
> {原文引用}

**差异描述**：
{LLM 分析结果}

**工作量提示**：
{实现成本影响说明}
```

---

## 五、系统架构

### 5.1 目录结构

```
C:\myfile\project\fh_protocol_compare\
├── DESIGN.md                   # 本文档
├── CHANGELOG.md                # 版本记录
├── config/
│   └── settings.yml            # LLM 配置 + 比对维度配置
├── src/
│   ├── __init__.py
│   ├── config_loader.py         # 配置读取
│   ├── llm_client.py            # LLM 调用（含多端点降级链）
│   ├── parser_pdf.py            # PDF 解析
│   ├── parser_docx.py           # Word 解析
│   ├── aligner.py               # 章节对齐
│   ├── differ.py                # 差异提取
│   ├── analyzer.py              # LLM 差异分析
│   └── reporter.py              # Markdown 报告生成
├── specs/                      # 结构化提取产物（按版本隔离）
│   └── {version}/
│       ├── base_spec.md
│       └── compare_spec.md
├── versions/                   # 比对结果归档
│   └── {yyyymmdd}_{base_name}_vs_{compare_name}/
│       ├── base_spec.md
│       ├── compare_spec.md
│       ├── alignment.json       # 章节对齐映射表
│       ├── diff_raw.md          # 原始 diff（含原文）
│       └── report.md            # 最终报告
├── input/                     # 原始文档（用户放置）
│   ├── base/
│   └── compare/
├── logs/                      # 日志
├── main.py                    # 主入口
└── requirements.txt
```

### 5.2 流程设计

```
① parse      PDF/Word → 结构化 Markdown（含章节层级）
② align      标题相似度匹配 → alignment.json（章节映射表）
③ diff       文本级 diff → diff_raw.md（含原文定位）
④ analyze    LLM 语义分析 → 分类 + 影响评估 + 溯源
⑤ report    汇总输出 → report.md
```

每个步骤的产物独立归档，步骤间可独立重跑。

### 5.3 章节对齐策略

> ⚠️ **重要约束**：O-RAN（O-RAN Alliance）和 ASTRI 是**不同标准组织**，章节编号和文档结构完全不同。严禁依赖章节编号做匹配。

- 提取每个文档的标题层级（章/节/子节）
- **基于语义相似度匹配**（TF-IDF + 余弦相似度），而非标题编号
- 引入关键词触发机制：识别以下核心功能领域的章节优先对齐
  - U-plane 字段定义（`uplane`, `U-Plane`, `user plane`, `UP Field`）
  - C-plane 字段定义（`cplane`, `C-Plane`, `control plane`, `CP Field`）
  - 信道传输映射（`channel mapping`, `transport`, `IQ Data`）
  - 消息结构（`message structure`, `IE`, `information element`）
  - 定时同步（`timing`, `synchronization`, `TDD`）
- 输出 `alignment.json`，记录 `base_section_id ↔ compare_section_id` 映射
- 未对齐章节单独标记（Base 独有 / Compare 独有）
- **语义覆盖度检查**：若某核心功能在两文档中均出现但未被对齐，人工告警

### 5.4 LLM 分析策略

**分段分析**，不一次性塞入全文：
- 以对齐后的章节对为单位，逐个调用 LLM
- 每个章节对单独生成若干差异点
- 最后汇总所有差异点，生成总览统计

---

## 六、LLM 配置

### 6.1 调用方案（降级链）

复用 arxiv_agent 的 `LLMClient`，支持以下调用方案，按优先级依次尝试：

| 优先级 | 方案 | 说明 |
|--------|------|------|
| 1 | OpenClaw 19000 proxy | 接受 modelroute 模型 |
| 2 | OpenClaw 28789 gateway | 接受 openclaw 模型 |
| 3 | 直连 API Key | 需在 settings.yml 中配置 |

19000 连续 2 次 403 后自动降级至 gateway。

### 6.2 不同任务选型

| 任务 | 建议模型 | 说明 |
|------|---------|------|
| 章节对齐 | 便宜模型 | 结构化匹配，不需要强推理 |
| 差异分析 | 强模型 | 需要语义理解和分类 |

可在 `settings.yml` 中为不同任务指定不同模型。

---

## 七、批量比对

### 7.1 模式

**模式 A（主流）**：Base 固定，Compare 多个并行

```
Base_v1 + Compare_v2 → report_1
Base_v1 + Compare_v3 → report_2
Base_v1 + Compare_v4 → report_3
```

下次批量时，Base 可重新指定。

### 7.2 一键执行

用户在 `config/settings.yml` 中配置待比对文件列表（Base 一个，Compare 多个），执行：

```powershell
py -3 main.py --batch
```

自动遍历全部 Compare 文档，依次完成比对，产物各自归档。

---

## 八、产物版本管理

- 版本目录命名：`{日期}_{base文档名}_vs_{compare文档名}`
- 若文档名过长，截取保留关键字
- 每次比对生成独立目录，不覆盖历史结果
- `versions/` 目录下所有子目录保留，不清理

---

## 九、OpenClaw 协作方式

| 方式 | 说明 |
|------|------|
| 纯命令行 | `py -3 main.py --base ... --compare ...`，独立运行，无需 Claw |
| Claw Skill 包装层（可选） | Skill 只做入口 + 通知，核心 pipeline 在脚本 |

核心逻辑全部在脚本中，Claw 负责触发和结果通知，两者解耦。

---

## 十、技术选型

| 模块 | 工具 | 备注 |
|------|------|------|
| PDF 解析（主） | `pdfplumber` 0.11.9 | 表格支持好，3GPP PDF 验证通过 |
| PDF 解析（备） | `pdfminer.six` | 复杂布局兜底 |
| PDF 渲染 | `pypdfium2` 5.7.0 | 图片/OCR 辅助 |
| Word 解析 | `python-docx` 1.2.0 | 标题层级提取 |
| 向量嵌入 | `sentence-transformers` 5.3.0 | all-MiniLM-L6-v2 已缓存（~18MB） |
| 章节对齐 | `scikit-learn` TF-IDF + 余弦相似度 | |
| 文本 diff（主） | `diff-match-patch`（vendor 内联） | Google 出品，PyPI 已下架，从 GitHub master 分支下载内联 |
| 文本 diff（备） | `difflib` | Python 标准库 |
| LLM 调用 | 本项目 `llm_client.py`（参考 arxiv_agent） | 多端点降级链 |
| 日志 | Python `logging` | 结构化日志，按日期滚动 |

---

## 十一、业界参考分析

### 11.1 现有工具调研结果

| 工具 | 类型 | 能力 | 不足 |
|------|------|------|------|
| `diff-match-patch`（Google） | 字符级 diff | 高速、多语言、广泛验证 | 无语义层、无结构化输出 |
| `elf_diff` | ELF 二进制符号比对 | 生成 HTML/PDF diff 报告 | 专用于二进制，不适用协议文档 |
| `scl-diff`（OpenEnergyTools） | SCL XML 元素比对 | 结构化对象描述 + 比对 | 专用于电力 SCL 格式，无语义分析 |
| `docdiff` | 纯文本 diff | 终端/浏览器可视化 | 无 LLM、无结构化输出 |
| `diff2html` | 代码 diff 渲染 | Git-style HTML 输出 | 纯文本 diff，无语义理解 |
| `ScholarMind_MAS` | 论文多智能体解读 | LLM + 结构化报告 + 多 Agent | 面向学术论文，无协议文档专化 |
| `git-cliff` | Changelog 生成 | Conventional commit 解析 | 面向代码提交历史，无文档比对 |
| O-RAN/3GPP 官方工具 | 规范发布 | 版本对照表 | 人工维护、无自动化分析 |

### 11.2 关键发现

1. **协议/规范文档比对领域，几乎没有成熟的自动化工具**
   - 现有工具要么是通用 diff，要么是专用于特定二进制/XML 格式
   - 没有任何一个工具同时具备：PDF/Word 解析 + 语义比对 + LLM 分析 + 结构化报告

2. **文本级 diff 无法满足协议比对需求**
   - 协议文档的差异点是**语义层面**的（功能、设计、参数），不是行级 diff
   - 需要 LLM 理解上下文后分类，而不是机械地找出哪行文本变了

3. **ScholarMind 是最接近的参考架构**
   - 多 Agent 协作（解析 → 分析 → 汇总 → 报告）
   - 结构化输出模型（structured_outputs.py）
   - 本质上类似我们 pipeline 的 ①→②→④→⑤ 流程

4. **LLM 降级链是工程实践中的标准做法**
   - arxiv_agent 的 LLMClient 实现了：API Key → 19000 proxy → gateway → 报错
   - 这是生产级 LLM 调用的必备保障

### 11.3 结论

本工程没有可直接复用的开源实现，属于**细分领域空白**。

我们设计的 pipeline（文档解析 → 章节对齐 → LLM 语义分析 → 结构化报告）是合理的，
参考了 ScholarMind 的多阶段 Agent 架构 + arxiv_agent 的 LLM 降级链 + 标准 diff 工具的对齐策略。

### 11.4 可借鉴的开源组件

| 组件 | 来源 | 用途 |
|------|------|------|
| `diff-match-patch`（Google） | github.com/google/diff-match-patch | 字符/词级 diff 兜底 |
| `PyMuPDF` | pip | PDF 文本提取 |
| `python-docx` | pip | Word 文档解析 |
| `scikit-learn` TF-IDF | pip | 章节标题相似度计算 |
| `LLMClient`（arxiv_agent） | 本地复用 | 多端点 LLM 调用 |
| ScholarMind `structured_outputs.py` | github.com/baifengbai/ScholarMind_MAS | 结构化报告模型参考 |

---

## 十二、待确认 / 开放问题

- [ ] 复杂排版（多栏、表格、跨页）是否需要特殊处理？
- [ ] 是否需要支持中文文档的全文比对？
- [ ] 报告中的"影响"等级是否有明确定义标准？

---

## 十二、变更记录

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-07-30 | v0.1 | 初稿，确立目录结构、流程设计、报告格式 |
