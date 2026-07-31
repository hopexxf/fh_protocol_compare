# FH Protocol Compare

> **5G NR 前传协议文档比对工程**
> 状态：✅ 核心功能完成
> 版本：v1.0
> 最后更新：2026-08-01

---

## 一、项目概述

### 1.1 目标

对比多份 5G NR 前传协议文档，以某一指定版本为 Base，分析另一个版本的**功能差异、设计差异、参数差异**，输出带原文溯源的 Markdown 报告，用于指导开发工作量评估。

### 1.2 输入输出

**输入**：
- Base 文档（基准版本，PDF 或 Word）
- Compare 文档（待比对版本，PDF 或 Word，支持多个）

**输出**：
- Markdown 差异报告（含原文溯源）
- 产物归档至 `versions/{yyyymmdd}_{base}_vs_{compare}/`

### 1.3 比对维度

| 维度 | 标签 | 说明 |
|------|------|------|
| 功能新增 | `feature-added` | Compare 有，Base 无 |
| 功能变更 | `feature-changed` | 两者均有但变化 |
| 功能删除 | `feature-removed` | Base 有，Compare 无 |
| 设计差异 | `design-diff` | 消息结构、流程变化 |
| 参数差异 | `param-diff` | 字段、阈值变化 |
| 范围差异 | `scope-diff` | 标准组织覆盖范围不同 |
| 一致性问题 | `consistency-issue` | 文档内部不自洽 |

---

## 二、系统架构

### 2.1 目录结构

```
C:\myfile\project\fh_protocol_compare\
├── README.md                   # 本文档
├── CHANGELOG.md                # 版本记录
├── requirements.txt            # 依赖清单
├── pytest.ini                  # pytest 配置
├── main.py                     # 主入口
├── self_check.py               # 自检脚本
├── config/
│   ├── settings.yml            # 配置文件（从 template 复制）
│   ├── settings.yml.template   # 配置模板
│   └── knowledge.yml           # 业务知识配置
├── src/
│   ├── __init__.py
│   ├── config_loader.py        # 配置读取
│   ├── llm_client.py           # LLM 调用（多端点降级）
│   ├── parser_pdf.py           # PDF 解析 + 表格提取
│   ├── parser_docx.py          # Word 解析
│   ├── aligner.py              # 章节对齐（TF-IDF）
│   ├── differ.py               # 差异提取（diff-match-patch）
│   ├── analyzer.py             # LLM 语义分析
│   └── reporter.py             # Markdown 报告生成
├── input/
│   ├── base/                   # Base 文档
│   └── compare/                # Compare 文档
├── versions/                   # 比对结果归档
├── logs/                       # 运行日志
├── vendor/
│   └── diff_match_patch.py     # diff-match-patch 源码
└── tests/                      # 测试套件
    ├── test_config.py
    ├── test_parser.py
    ├── test_aligner.py
    ├── test_differ.py
    ├── test_analyzer.py
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
- 支持 stream/lattice 模式
- 表格自动关联章节
- 输出 Markdown 格式（含页码溯源）

**LLM 分析**：
- 动态知识注入（21 个 diff_patterns）
- 多端点降级链（19000 → 61791 → AWS）
- 异步并发批量分析

---

## 三、配置说明

### 3.1 环境准备

**依赖环境**：
- Python 3.12+
- Java JRE 25（camelot 表格提取）
- camelot-py 2.0.0

**安装步骤**：
```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制配置文件
cp config/settings.yml.template config/settings.yml

# 3. 填写 LLM 配置（如使用 OpenClaw Gateway）
# 编辑 config/settings.yml
```

### 3.2 核心配置项

**LLM 配置**（`config/settings.yml`）：
```yaml
llm:
  use_openclaw: true          # 使用 OpenClaw 集成
  model: "openclaw"
  temperature: 0.3
  max_tokens: 1500
  timeout: 120
```

**PDF 配置**：
```yaml
pdf:
  use_camelot: true           # 启用 camelot 表格提取
  camelot_flavor: "stream"    # stream（无需 Ghostscript）或 lattice
  table_min_accuracy: 80      # 表格准确度阈值
  extract_tables: true
  insert_tables_to_sections: true  # 表格插回章节
```

**业务知识配置**（`config/knowledge.yml`）：
- `org_background`：标准组织背景（O-RAN vs ASTRI）
- `layer_responsibility`：协议分层职责（U/C/S/M-plane）
- `diff_patterns`：21 个关键词映射（priority 1-10）

---

## 四、使用方法

### 4.1 单次比对

```bash
# 方式 1：交互式
python main.py

# 方式 2：命令行参数
python main.py --base input/base/O-RAN.pdf --compare input/compare/ASTRI.pdf
```

### 4.2 批量比对

```bash
# 编辑 config/settings.yml
batch:
  base_file: "input/base/O-RAN.pdf"
  compare_files:
    - "input/compare/ASTRI_v1.pdf"
    - "input/compare/ASTRI_v2.pdf"

# 执行
python main.py --batch
```

### 4.3 运行测试

```bash
# 全量测试（排除慢测试）
pytest tests/ -v -m "not slow"

# 全量测试（含 PDF 解析）
pytest tests/ -v

# 单模块测试
pytest tests/test_analyzer.py -v
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

**算法**：diff-match-patch（Google）

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

**方案选择**：camelot（方案 3）

**理由**：
- 协议文档表格特点（格线清晰、合并单元格、跨页）正是 camelot 强项
- Java 依赖可接受（一次性成本）
- 上下文问题可后处理（页码 + 文本匹配）

**实现**：
- `extract_tables_camelot()`：支持 stream/lattice 模式
- `_associate_tables_with_sections()`：页码关联 + 文本匹配
- `_insert_tables_into_sections()`：插回章节末尾

---

## 六、测试状态

### 6.1 测试覆盖

| 模块 | 测试文件 | 用例数 | 状态 |
|------|----------|--------|------|
| 配置加载 | test_config.py | 12 | ✅ |
| 文档解析 | test_parser.py | 13 | ✅ (含 11 slow) |
| 章节对齐 | test_aligner.py | 26 | ✅ (含 2 slow) |
| 差异提取 | test_differ.py | 35 | ✅ |
| LLM 分析 | test_analyzer.py | 26 | ✅ |
| 报告生成 | test_reporter.py | 32 | ✅ |

**总计**：144 passed, 13 deselected (slow)

### 6.2 测试样本

| 文件 | 路径 | 大小 | 页数 |
|------|------|------|------|
| O-RAN 规范 | input/base/O-RAN.WG4.CUS.0-v05.00.pdf | 8.3 MB | 292 |
| ASTRI 架构设计 | input/compare/ASTRI_NRBS_L1_...pdf | 0.54 MB | 27 |

---

## 七、已知问题与改进

### 7.1 已修复（2026-08-01）

| 问题 | 状态 | 提交 |
|------|------|------|
| 报告缺页码溯源 | ✅ 已修复 | e2a50eb |
| Base 独有章节硬编码 feature-removed | ✅ 已修复 | e2a50eb |
| requirements.txt 缺 httpx | ✅ 已修复 | e2a50eb |
| PDF 表格结构丢失 | ✅ 已修复 | f4295c3 |
| 表格无上下文 | ✅ 已修复 | e7f4d83 |

### 7.2 待改进

| 问题 | 优先级 | 说明 |
|------|--------|------|
| 语义覆盖度检查 | P1 | 检测核心功能未被对齐 |
| 按任务选型模型 | P2 | 对齐用便宜模型、分析用强模型 |
| DOCX 表格提取 | P2 | 当前仅 PDF 支持表格 |

---

## 八、依赖清单

```txt
# 文档解析
pdfplumber>=0.11.0
pdfminer.six>=20231228
pypdfium2>=4.0
python-docx>=1.0.0

# 表格提取（基于 Java）
camelot-py>=2.0.0

# 相似度计算（TF-IDF + 余弦）
scikit-learn>=1.3.0

# 配置
PyYAML>=6.0

# HTTP 请求（LLM 调用）
requests>=2.31.0
httpx>=0.24.0
```

---

## 九、性能数据

### 9.1 测试执行

- **核心测试**：133 passed, 3.60s
- **全量测试**：144 passed, ~155s（含 PDF 解析）

### 9.2 表格提取

- **ASTRI（27 页）**：36 个表格，准确度 95-100%
- **关联成功率**：100%（36/36）

### 9.3 LLM 分析

- **单次调用**：~45s（含流式解析）
- **异步并发**：concurrency=10

---

## 十、版本历史

详见 `CHANGELOG.md`

**关键版本**：
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
