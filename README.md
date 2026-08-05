# FH Protocol Compare

> **5G NR 前传协议文档比对工程**
> 状态：✅ 核心功能完成
> 版本：v1.2
> 最后更新：2026-08-05

---

## 一、项目概述

### 1.1 目标

对比多份 5G NR 前传协议文档，以某一指定版本为 Base，分析另一个版本的**功能差异、设计差异、参数差异**，输出带原文溯源的 Markdown 报告，用于指导开发工作量评估。

### 1.2 输入输出

**输入**：
- Base 文档（基准版本，PDF 或 Word）
- Compare 文档（待比对版本，PDF 或 Word，支持多个）

**输出**：
- Markdown 差异报告（含原文溯源：文档名 + 章节 + 页码 + 段落号）
- 产物归档至 `versions/{yyyymmdd}_{base}_vs_{compare}/`

### 1.3 比对维度

| 维度 | 标签 | 说明 |
|------|------|------|
| 功能新增 | `feature_added` | Compare 有，Base 无 |
| 功能变更 | `feature_changed` | 两者均有但变化 |
| 功能删除 | `feature_removed` | Base 有，Compare 无（经 LLM 判断，非硬编码） |
| 设计差异 | `design_diff` | 消息结构、流程变化 |
| 参数差异 | `param_diff` | 字段、阈值变化 |
| 范围差异 | `scope_diff` | 标准组织覆盖范围不同（O-RAN 有 / ASTRI 无） |
| 一致性问题 | `consistency_issue` | 文档内部不自洽 |
| 未知差异 | `unknow_diff` | LLM 异常时对齐章节的兜底分类（需重跑确认） |

---

## 二、系统架构

### 2.1 目录结构

```
C:\myfile\project\fh_protocol_compare\
├── README.md                   # 本文档
├── CHANGELOG.md                # 版本记录
├── requirements.txt            # 依赖清单
├── pytest.ini                  # pytest 配置（注册 slow marker）
├── main.py                     # 主入口（CLI）
├── self_check.py               # 自检脚本
├── config/
│   ├── settings.yml            # 配置（从 template 复制，含本地路径，不提交）
│   ├── settings.yml.template   # 配置模板（提交）
│   └── knowledge.yml           # 业务知识（动态注入 LLM）
├── src/
│   ├── __init__.py
│   ├── config_loader.py        # 配置读取 + 业务知识加载
│   ├── llm_client.py           # LLM 调用（OpenClaw Gateway 封装）
│   ├── parser_pdf.py           # PDF 解析 + camelot 表格提取
│   ├── parser_docx.py          # Word 解析
│   ├── aligner.py              # 章节对齐（TF-IDF + 余弦）
│   ├── differ.py               # 差异提取（diff-match-patch）
│   ├── analyzer.py             # LLM 语义分析
│   ├── reporter.py             # Markdown 报告生成
│   ├── filter_boilerplate.py   # 思路1：Boilerplate 章节过滤（省 LLM 调用）
│   ├── cluster.py              # 思路2：相似聚类，代表项共享分析（省 LLM 调用）
│   └── abstractor.py           # 思路3：报告浓缩 + 执行摘要生成
├── input/
│   ├── base/                   # Base 文档（gitignore）
│   └── compare/                # Compare 文档（gitignore）
├── versions/                   # 比对结果归档（gitignore）
├── logs/                       # 运行日志（gitignore）
├── vendor/
│   └── diff_match_patch.py     # diff-match-patch 源码（仓库跟踪）
└── tests/                      # 测试套件
    ├── test_config.py
    ├── test_parser.py
    ├── test_parser_docx_return.py
    ├── test_aligner.py
    ├── test_differ.py
    ├── test_analyzer.py
    ├── test_analyzer_robustness.py   # 纯函数回归（无网络）
    ├── test_gateway_port.py          # Gateway 端口发现 + 1 个实时 LLM 调用
    └── test_reporter.py
```

### 2.2 核心流程

```
1/6 文档解析 → 2/6 章节对齐 → 2.5/6 Boilerplate过滤 → 3/6 差异提取 → 4/6 LLM分析 → 5/6 报告生成 → 6/6 执行摘要
    ↓          ↓               ↓                    ↓           ↓            ↓              ↓
parser_*   aligner    filter_boilerplate      differ       analyzer     reporter     abstractor
```

> filter / cluster / abstract 模块为可选开关（默认关闭），开启时自动插入对应步骤。

### 2.3 关键特性

**PDF 表格提取**（camelot）：
- 支持 stream（无需 Ghostscript）/ lattice（需 Ghostscript，精度更高）模式
- 表格自动关联章节，插回章节末尾（含页码溯源）
- 低准确度表格告警（阈值 `table_min_accuracy`）

**LLM 语义分析**（OpenClaw Gateway）：
- 动态知识注入：`knowledge.yml` 中 21 个 `diff_patterns`，按 priority 降序取前 3 注入
- 端口与 Bearer Token 从 `~/.qclaw/openclaw.json` **每次请求动态读取**（Gateway 重启端口会漂移）
- 非流式请求（`stream: False`），单字段内容截断至 3000 字符（防止 Gateway 对超长请求挂死）
- 并发数默认 1（Gateway 并发 >1 易挂死），失败自动重试 3 次（2s 递增退避）

---

### 2.4 可选优化模块（思路 1/2/3，默认关闭）

三个模块均为**可选开关**，默认 `enabled: false`，互不影响，可任意组合。目标均为降低 LLM 调用成本或提升报告可用性。

| 思路 | 模块 | 插入位置 | 作用 | 实测节省 |
|------|------|----------|------|----------|
| 1 Boilerplate 过滤 | `filter_boilerplate.py` | align 后 / diff 前 | 硬删版权/目录/参考文献等章节级噪声，省 LLM 调用 + 净化报告 | 章节级 ~4%（真实数据 801 节中 34 节） |
| 2 相似聚类 | `cluster.py` | diff 后 / LLM 前 | 高阈值 TF-IDF 余弦连通分量聚类，代表项共享分析 | 阈值 0.88 省 18.9%（809→656 代表项） |
| 3 摘要生成 | `abstractor.py` | report 后 | 浓缩报告（去 quote）→ LLM 生成精简执行摘要 | 报告体积大幅下降；聚焦高影响项 |

**Abstract 输出结构**（`report_abstract.md`）：工作量定位（颠覆性/重大/局部/无影响）→ 关键差异详述（高影响 top 3-5，维度标签）→ 其余差异简表（按类型合并）→ 验证优先级建议。

> 历史数据测量：20260801 归档（809 条 diff_raw）聚类 0.88 → 656 代表项；章节级 boilerplate 率 4.2%。
> 开关见 §3.2 `filter` / `cluster` / `abstract` 配置段；默认全关，启用即在流程中生效。
> `report_abstract.md` 依赖 abstract 模块开启，聚焦工作量定位与关键差异详述（详见 §2.3 Abstract 结构）。

## 三、配置说明

### 3.1 环境准备

**依赖环境**：
- Python 3.12+
- Java JRE ≥ 1.8（camelot 表格提取，仅需 JRE 无需 JDK）
- camelot-py 2.0.0 + opencv-python
- **OpenClaw Gateway 运行中**（LLM 分析依赖 `~/.qclaw/openclaw.json` 的端口与 token）

**安装步骤**：
```bash
# 1. 安装依赖
py -3 -m pip install -r requirements.txt

# 2. 复制配置文件（settings.yml 含本地路径，不提交）
cp config/settings.yml.template config/settings.yml

# 3. 确认 OpenClaw Gateway 已启动，端口/token 会自动从 openclaw.json 读取
```

### 3.2 核心配置项

**LLM 配置**（`config/settings.yml`）：
```yaml
llm:
  use_openclaw: true          # 使用 OpenClaw Gateway（推荐）
  model: "gpt-3.5-turbo"     # 仅直连模式（use_openclaw:false）使用
  gateway_port: 53311         # 仅作回退；实际端口从 openclaw.json 动态读取
  temperature: 0.3
  max_tokens: 1500
  timeout: 120
```
> ⚠️ OpenClaw Gateway 模式下，`model` 由代码固定为 `"openclaw"`，`gateway_port` 仅作回退值；
> 真实端口与 Bearer Token 每次请求从 `~/.qclaw/openclaw.json` 实时读取，重启 Gateway 后**无需改配置**。

**PDF 表格配置**：
```yaml
pdf:
  use_camelot: true              # 启用 camelot 表格提取
  camelot_flavor: "stream"       # stream（无需 Ghostscript）或 lattice（需 Ghostscript）
  table_min_accuracy: 80         # 表格准确度阈值（低于则告警）
  extract_tables: true
  insert_tables_to_sections: true  # 表格插回章节
```

**Boilerplate / 聚类 / 摘要配置**（均默认关闭，详见 §2.4）：
```yaml
filter:
  enabled: false               # 启用 Boilerplate 过滤
  skip_boilerplate: true       # 按双语黑名单 + 内容信号过滤章节级噪声
  boilerplate_keywords: []     # 可追加自定义关键词（小写子串匹配标题/内容）
  skip_front_pages: 0          # 可选：跳过年 prefix 页数
  skip_back_pages: 0           # 可选：跳过末尾页数
cluster:
  enabled: false               # 启用相似聚类
  similarity_threshold: 0.88   # TF-IDF 余弦连通分量阈值（0.85~0.90 区间）
  method: tfidf_cosine
abstract:
  enabled: false               # 启用报告摘要
  focus: development_cost      # 聚焦开发成本
  max_tokens: 4000
  top_n: 10                    # 摘要聚焦前 N 个高影响差异
```

**业务知识配置**（`config/knowledge.yml`）：
- `org_background`：标准组织背景（O-RAN vs ASTRI），scope_diff 判断核心
- `layer_responsibility`：协议分层职责（U/C/S/M-plane），impact 判断核心
- `diff_patterns`：21 个关键词映射（priority 1-10），运行时按内容动态匹配注入

---

## 四、使用方法

### 4.1 单次比对

```bash
# 必须指定 --base 与 --compare（不支持无参数交互式）
py -3 main.py --base input/base/O-RAN.WG4.CUS.0-v05.00.pdf ^
              --compare input/compare/ASTRI_xxx.pdf

# 子集快速验证（仅分析前 N 个差异条目）
py -3 main.py --base input/base/O-RAN.pdf --compare input/compare/ASTRI.pdf --max-items 30

# 强制并发（默认 1；Gateway 并发 >1 易挂死，完整 809 条约需数小时）
py -3 main.py --base input/base/O-RAN.pdf --compare input/compare/ASTRI.pdf --concurrency 1

# 从已有版本目录恢复（跳过 Step 1-4，仅重生成报告）
# 用于断网后产物丢失或仅修改报告模板后重新生成
py -3 main.py --resume versions/20260805_O-RAN_vs_ASTRI
```

### 4.2 批量比对

编辑 `config/settings.yml` 的 `batch` 段后执行：
```bash
py -3 main.py --batch
```
```yaml
batch:
  base_file: "input/base/O-RAN.pdf"
  compare_files:
    - "input/compare/ASTRI_v1.pdf"
    - "input/compare/ASTRI_v2.pdf"
```

### 4.3 运行测试

```bash
# 排除慢测试（PDF 解析，Windows 下易触发内存 SIGKILL）
pytest tests/ -v -m "not slow"

# 仅跑两个改动的测试文件（含 1 个实时 LLM 调用）
pytest tests/test_analyzer_robustness.py tests/test_gateway_port.py -v

# 含 PDF 解析的全量（注意内存，建议大内存机器）
pytest tests/ -v
```

---

## 五、设计决策

### 5.1 章节对齐策略

**算法**：TF-IDF + 余弦相似度 + 关键词得分

**步骤**：
1. 提取章节标题（正则匹配编号前缀）
2. 计算 TF-IDF 向量
3. 计算余弦相似度
4. 相似度 ≥ 阈值（0.3）视为匹配
5. 未匹配章节标记为独有

### 5.2 差异检测策略

**算法**：diff-match-patch（Google，源码在 `vendor/`）

**步骤**：
1. 对比对章节文本
2. 计算 diff 操作序列
3. 过滤噪声（min_change_chars=30）
4. 输出差异片段列表

### 5.3 LLM 分析策略

**动态知识注入**：
- 模块 1（~100 tokens）：标准组织背景
- 模块 2（~100 tokens）：协议分层职责
- 模块 5（~50-100 tokens）：动态关键词提示（按 priority 降序取前 3）

**总注入量**：~250-300 tokens/次（比逐章节注入减少 60%）

### 5.4 表格提取策略

**方案**：camelot（stream 模式无需 Ghostscript，协议文档格线清晰适合 lattice/stream）

**实现**：
- `extract_tables_camelot()`：stream/lattice 模式，返回 page/table/accuracy/bbox
- `_associate_tables_with_sections()`：页码关联 + 文本匹配
- `_insert_tables_into_sections()`：插回章节末尾（从后向前避免行号偏移）

### 5.5 LLM / Gateway 调用约束（排障经验，必读）

以下约束源自真实排障，违反会导致 LLM 分析**全部失败或挂死**：

| 约束 | 原因 | 实现位置 |
|------|------|----------|
| 端口/Token 动态读取 | Gateway 重启端口漂移（61791→53311→51900…） | `llm_client._get_gateway_port` / `_load_gateway_token` |
| 非流式 `stream: False` | 流式在并发下 Gateway 无限挂死（读超时不触发） | `analyzer.fetch_one` |
| 内容截断 3000 字符 | 单请求 >~8K 字符 Gateway 挂死 | `analyzer.MAX_LLM_CONTENT_CHARS` |
| 并发 = 1 | Gateway 并发 >1 请求挂死，150s 超时 ×3 重试耗尽 | `main.py --concurrency`（默认 1） |
| 响应兼容 JSON/SSE | Gateway 对 stream:False 返回格式不稳定 | `analyzer._extract_content_from_response` |
| 异常带类型名 | httpcore ReadTimeout.str() 返回空串，失败原因静默丢失 | `analyzer._fmt_err` |
| 测试后清会话 | 无 `user` 字段的请求会累积临时会话 | `openclaw sessions cleanup --enforce` |
| 熔断机制（连续失败 ≥阈值） | 断网时快速兜底，避免空转数小时 | `analyzer.fetch_one`（阈值 = max(concurrency*2, 5)） |
| 不可重试错误即时熔断 | 408/ReadTimeout/ConnectError 不重试 | `analyzer._is_non_retryable_error` |

**熔断行为**：
- 连续失败 ≥ 阈值后停止 LLM 调用，剩余条目走兜底结果
- 兜底结果按章节类型分类：Base 独有 → `feature_removed`，Compare 独有 → `feature_added`，对齐章节 → `unknow_diff`
- 报告正常生成，但差异类型含 "LLM 调用失败" 字样，需联网重跑确认

**断网产物归档**：
- Step 4.5 在分析完成后**立即归档**中间产物（base_spec.md / compare_spec.md / alignment.json / diff_raw.json / analyzed.json / stats.json）
- Step 5/6 失败不影响中间产物，可用 `--resume` 从已有目录恢复

**测试会话清理**：`tests/test_gateway_port.py` 的真实 LLM 调用带 `user: "test_gateway_port"` 字段，
运行后需执行 `openclaw sessions cleanup --enforce` 清理（近期会话受 30 天保留策略约束，可能不立即清除）。

---

## 六、测试状态

### 6.1 测试覆盖

| 模块 | 测试文件 | 用例数 | 状态 |
|------|----------|--------|------|
| 配置加载 | test_config.py | 12 | ✅ |
| 文档解析(PDF) | test_parser.py | 13 | ✅（含 slow） |
| 文档解析(DOCX) | test_parser_docx_return.py | 2 | ✅ |
| 章节对齐 | test_aligner.py | 26 | ✅（含 2 slow） |
| 差异提取 | test_differ.py | 35 | ✅ |
| LLM 分析 | test_analyzer.py | 26 | ✅ |
| LLM 健壮性回归 | test_analyzer_robustness.py | 15 | ✅（纯函数，无网络） |
| Boilerplate 过滤 | test_filter_boilerplate.py | 12 | ✅ |
| 相似聚类 | test_cluster.py | 7 | ✅（零 LLM） |
| 报告摘要 | test_abstractor.py | 4 | ✅（mock LLM） |
| Gateway 端口发现 | test_gateway_port.py | 4 | ✅（含 1 个实时 LLM 调用） |
| 报告生成 | test_reporter.py | 32 | ✅ |

**总计**：184 passed, 13 deselected (slow)

### 6.2 测试样本

| 文件 | 路径 | 大小 | 页数 |
|------|------|------|------|
| O-RAN 规范 | input/base/O-RAN.WG4.CUS.0-v05.00.pdf | 8.3 MB | 292 |
| ASTRI 架构设计 | input/compare/ASTRI_NRBS_L1_...pdf | 0.54 MB | 27 |

---

## 七、已知问题与改进

### 7.1 已修复

| 问题 | 状态 | 提交 |
|------|------|------|
| 报告缺页码溯源 | ✅ | e2a50eb |
| Base 独有章节硬编码 feature_removed | ✅（改走 LLM 判断） | e2a50eb |
| requirements.txt 缺 httpx | ✅ | e2a50eb |
| PDF 表格结构丢失 | ✅（camelot） | f4295c3 |
| 表格无上下文 | ✅（关联章节） | e7f4d83 |
| Gateway 端口漂移致 LLM 全失败 | ✅（动态读取） | b7a7fd6 / baf8c1a |
| 超长请求 Gateway 挂死 | ✅（截断 3000） | b4a76c2 |
| 流式并发挂死 / 响应格式 flaky | ✅（非流式 + 兼容解析） | a6e2fc 段 |
| ASTRI 表格章节化导致对齐错配 | ✅（表格归属修复 + TOC 过滤） | 494a35b |
| 目录行 / "Through" 进对齐噪声 | ✅（_is_toc_entry + TOC_CONTENT_SIGNALS） | 494a35b |
| 表格页码无法溯源至报告 | ✅（_extract_table_page_hint 透传） | 494a35b |
| Abstract 格式简陋（无工作量定位） | ✅（重设计为三级+维度详述+简表） | 494a35b |
| 断网卡死（623项×150s×3重试≈26h） | ✅（熔断+短超时+不可重试即时失败） | e0b9dd4 / 3236ba5 |
| 熔断后仍发 HTTP（semaphore 外检查） | ✅（检查移入 async with sem 内） | e0b9dd4 |
| 断网时产物丢失（Step 6 崩溃） | ✅（Step 4.5 立即归档中间产物） | 0b88fa0 |
| Compare 独有无兜底分支 | ✅（feature_added 兜底） | 5842d7f |
| 对齐章节 LLM 异常被丢弃 | ✅（unknow_diff 非空 diffs） | 5842d7f |

### 7.2 待改进

| 问题 | 优先级 | 说明 |
|------|--------|------|
| 语义覆盖度检查 | P1 | 检测核心功能未被对齐 |
| 按任务选型模型 | P2 | 对齐用便宜模型、分析用强模型 |
| DOCX 表格提取 | P2 | 当前仅 PDF 支持表格提取 |

---

## 八、依赖清单

```txt
# 文档解析
pdfplumber>=0.11.0
pdfminer.six>=20231228
python-docx>=1.0.0

# 表格提取（基于 Java / camelot）
camelot-py>=2.0.0
opencv-python            # camelot cv 后端

# 相似度计算（章节对齐，TF-IDF + 余弦）
scikit-learn>=1.3.0

# 配置
PyYAML>=6.0

# HTTP 请求（LLM 调用）
requests>=2.31.0      # 同步调用（llm_client.py）
httpx>=0.24.0         # 异步并发批量（analyzer.py）

# 文本 diff（从 vendor/ 加载，无需 pip 安装）
# diff_match_patch — 源码在 vendor/diff_match_patch.py，已纳入仓库
```

---

## 九、性能数据

### 9.1 测试执行

- **核心测试**（排除 slow）：184 passed, 13 deselected, ~45s
- **全量测试**（含 PDF 解析 slow 用例）：约 197 用例，视机器内存

### 9.2 表格提取

- **ASTRI（27 页）**：36 个表格，准确度 95-100%
- **关联成功率**：100%（36/36）

### 9.3 LLM 分析

- **单请求**：20-70s（非流式）
- **并发**：默认 1（Gateway 并发 >1 易挂死）；失败重试 3 次
- **完整比对**（809 差异章节，并发 1）：约数小时

---

## 十、版本历史

详见 `CHANGELOG.md`

**关键版本**：
- **v1.2**（2026-08-05）：断网熔断修复、兜底结果分类（feature_added/unknow_diff）、--resume 参数、中间产物提前归档
- **v1.1**（2026-08-03）：LLM/Gateway 调用约束固化、测试回归、文档与配置整理
- **v1.0**（2026-08-01）：核心功能完成，表格提取方案完成
- **v0.1**（2026-07-30）：工程框架搭建

---

## 十一、参考资料

- O-RAN Alliance: https://www.o-ran.org/
- eCPRI Specification: https://www.ecpri.org/
- 3GPP TS 38 Series: https://www.3gpp.org/

---

## 十二、作者

- **通信大佬**：项目负责人
- **通信小粉**：AI 助手
