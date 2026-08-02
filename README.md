# FH Protocol Compare

> **5G NR 前传协议文档比对工程**
> 状态：✅ 核心功能完成
> 版本：v1.1
> 最后更新：2026-08-03

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
| 功能新增 | `feature-added` | Compare 有，Base 无 |
| 功能变更 | `feature-changed` | 两者均有但变化 |
| 功能删除 | `feature-removed` | Base 有，Compare 无（经 LLM 判断，非硬编码） |
| 设计差异 | `design-diff` | 消息结构、流程变化 |
| 参数差异 | `param-diff` | 字段、阈值变化 |
| 范围差异 | `scope-diff` | 标准组织覆盖范围不同（O-RAN 有 / ASTRI 无） |
| 一致性问题 | `consistency-issue` | 文档内部不自洽 |

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
│   └── reporter.py             # Markdown 报告生成
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
文档解析 → 章节对齐 → 差异提取 → LLM分析 → 报告生成
    ↓          ↓          ↓          ↓          ↓
parser_*   aligner    differ    analyzer   reporter
```

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

**业务知识配置**（`config/knowledge.yml`）：
- `org_background`：标准组织背景（O-RAN vs ASTRI），scope-diff 判断核心
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
| Gateway 端口发现 | test_gateway_port.py | 4 | ✅（含 1 个实时 LLM 调用） |
| 报告生成 | test_reporter.py | 32 | ✅ |

**总计**：约 152 passed, 13 deselected (slow)

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
| Base 独有章节硬编码 feature-removed | ✅（改走 LLM 判断） | e2a50eb |
| requirements.txt 缺 httpx | ✅ | e2a50eb |
| PDF 表格结构丢失 | ✅（camelot） | f4295c3 |
| 表格无上下文 | ✅（关联章节） | e7f4d83 |
| Gateway 端口漂移致 LLM 全失败 | ✅（动态读取） | b7a7fd6 / baf8c1a |
| 超长请求 Gateway 挂死 | ✅（截断 3000） | b4a76c2 |
| 流式并发挂死 / 响应格式 flaky | ✅（非流式 + 兼容解析） | a6e2fc 段 |

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

- **核心测试**（排除 slow）：约 152 passed, 3.6s
- **全量测试**（含 PDF 解析）：约 165 用例，视机器内存

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
