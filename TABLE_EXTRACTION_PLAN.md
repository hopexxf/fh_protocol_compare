# PDF 表格提取方案策划（方案 3：Camelot）

**策划时间**: 2026-08-01 01:15
**方案编号**: 方案 3
**预计总工时**: 10-11 小时
**完成时间**: 2026-08-01 02:25
**实际工时**: 约 3 小时

---

## 执行状态：✅ 全部完成

| 阶段 | 状态 | 提交 | 说明 |
|------|------|------|------|
| **Phase 0** | ✅ 完成 | - | JRE 25 + camelot 2.0.0，stream 模式无需 Ghostscript |
| **Phase 1** | ✅ 完成 | f4295c3 | 36 个表格提取，准确度 95-100% |
| **Phase 2** | ✅ 完成 | e7f4d83 | 36/36 表格关联成功，插回 6 个章节 |
| **Phase 3** | ✅ 完成 | f25ba90 | 133 passed, 13 deselected (3.60s) |

---

## 一、背景与目标

### 当前问题

- PDF 表格通过 `pdfplumber.extract_text()` 提取，**结构丢失**
- 表格被逐行拆解为无结构文本片段
- 差异检测失准，LLM 分析困难，溯源定位失效

### 目标

- 保留表格结构（Markdown 格式）
- 保留表格上下文（所在章节）
- 支持协议文档常见表格类型（参数定义表、嵌套表格、跨页表格）

---

## 二、方案对比（为何选方案 3）

| 维度 | 方案 2（原地保留） | 方案 3（Camelot 专用库） |
|------|-------------------|-------------------------|
| **实现复杂度** | 高（需处理表格定位、文本去重、区域排除） | 中（库封装好，直接调用） |
| **表格识别精度** | 依赖 pdfplumber，一般 | **更高**（camelot 专为此设计） |
| **复杂表格支持** | 差（合并单元格、跨页表格难处理） | **好**（camelot 支持流式/格点模式） |
| **上下文保留** | 好（表格在原章节内） | 差（需后处理插回原位） |
| **依赖环境** | 无额外依赖 | camelot 需 Java |
| **维护成本** | 高（自定义逻辑多） | **低**（库维护） |

**选择理由**：
- 协议文档表格特点（格线清晰、合并单元格、跨页表格）正是 camelot 强项
- Java 依赖可接受（一次性成本）
- 上下文问题可后处理（页码 + 文本匹配）

---

## 三、阶段划分

| 阶段 | 目标 | 交付物 | 工作量 | 验证方式 |
|------|------|--------|--------|----------|
| **Phase 0** | 环境准备 | Java 环境检查 + camelot 安装 | 0.5h | 运行测试脚本 |
| **Phase 1** | 基础提取 | 表格提取 + Markdown 转换 | 2-3h | 单元测试 + 小规模 PDF |
| **Phase 2** | 上下文保留 | 表格插回章节 | 3-5h | 对比验证（前后效果） |
| **Phase 3** | 集成测试 | 全量测试 + 文档更新 | 1-2h | pytest 全量通过 |

**总计**: 约 10-11 小时（分 2-3 个工作日完成）

---

## 四、Phase 0：环境准备

### 目标
确保 Java 环境可用，安装 camelot-py 及依赖。

### 任务清单

| 任务 | 命令/操作 | 验证 |
|------|----------|------|
| 检查 Java | `java -version` | 输出 ≥ 1.8 |
| 安装 camelot-py | `pip install camelot-py[cv]` | 无报错 |
| 安装 OpenCV 依赖 | `pip install opencv-python` | 无报错 |
| 验证安装 | 运行测试脚本 | 提取成功 |

### 测试脚本

```python
# test_camelot_install.py
import camelot

tables = camelot.read_pdf("input/base/O-RAN-WG4.CUS.0-v08.00.pdf", pages='1', flavor='lattice')
print(f"提取到 {len(tables)} 个表格")
if tables:
    print(tables[0].df.head())
```

### 回退方案

如 camelot 安装失败（Java 环境问题），保留 pdfplumber 表格提取作为降级：

```python
try:
    import camelot
    USE_CAMELOT = True
except ImportError:
    USE_CAMELOT = False
    logger.warning("[PDF] camelot 不可用，使用 pdfplumber 降级")
```

---

## 五、Phase 1：基础表格提取

### 目标
使用 camelot 提取表格，转换为 Markdown 格式，统一追加到文档末尾。

### 设计

#### 5.1 配置扩展

```yaml
# config/settings.yml.template 新增
pdf:
  use_camelot: true              # 是否启用 camelot
  camelot_flavor: "lattice"      # lattice（有格线）或 stream（无格线）
  table_min_accuracy: 80         # 表格提取准确度阈值（低于此值告警）
  fallback_to_pdfplumber: true   # camelot 失败时是否回退到 pdfplumber
```

#### 5.2 代码实现

**新增函数**：`src/parser_pdf.py`

```python
def extract_tables_camelot(pdf_path: str, flavor: str = "lattice") -> list[dict]:
    """
    使用 camelot 提取表格（高精度）。
    
    Args:
        pdf_path: PDF 文件路径
        flavor: 'lattice'（有格线）或 'stream'（无格线）
    
    Returns:
        list[dict]: [
            {
                "page_num": int,
                "table_index": int,
                "table": list[list[str]],  # 二维数组
                "accuracy": float,         # 提取准确度
                "bbox": tuple,             # 表格边界 (x1, y1, x2, y2)
            },
            ...
        ]
    """
    import camelot
    
    tables = camelot.read_pdf(pdf_path, pages='all', flavor=flavor)
    
    results = []
    for i, t in enumerate(tables):
        results.append({
            "page_num": t.page,
            "table_index": i,
            "table": t.df.values.tolist(),
            "accuracy": t.accuracy,
            "bbox": t._bbox,
        })
    
    logger.info(f"[Camelot] 提取到 {len(results)} 个表格")
    return results


def _table_to_markdown(table_data: list[list[str]], caption: str = "") -> str:
    """
    将单个表格转换为 Markdown 格式。
    
    Args:
        table_data: 二维数组（含表头）
        caption: 表格标题（可选）
    
    Returns:
        Markdown 格式的表格字符串
    """
    if not table_data or len(table_data) < 1:
        return ""
    
    lines = []
    
    # 表格标题
    if caption:
        lines.append(f"**{caption}**\n")
    
    # 表头
    header = table_data[0]
    lines.append("| " + " | ".join(str(cell).strip() for cell in header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    
    # 表体
    for row in table_data[1:]:
        cells = [str(cell).strip() if cell else "" for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    
    return "\n".join(lines) + "\n"


def _tables_to_markdown(tables: list[dict]) -> str:
    """
    将表格列表转换为 Markdown 格式（批量）。
    
    每个表格标注页码，便于溯源。
    """
    if not tables:
        return ""
    
    lines = ["\n\n---\n\n## 表格列表\n"]
    
    for tbl in tables:
        caption = f"表格 {tbl['page_num']}-{tbl['table_index']}（P{tbl['page_num']}，准确度 {tbl['accuracy']:.1f}%）"
        lines.append(f"\n### {caption}\n\n")
        lines.append(_table_to_markdown(tbl["table"]))
        lines.append(f"\n<!-- table_page={tbl['page_num']} table_index={tbl['table_index']} -->\n")
    
    return "\n".join(lines)
```

**修改主函数**：`src/parser_pdf.py`

```python
def parse_pdf(
    pdf_path: str,
    *,
    use_miner_fallback: bool = True,
    extract_tables: bool = True,  # 新增参数
) -> tuple[str, list[dict], list[dict]]:
    """
    解析 PDF 文档。
    
    Returns:
        (markdown: str, pages: list[dict], tables: list[dict])
    """
    import os
    
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
    
    logger.info(f"[PDF] 开始解析: {pdf_path}")
    
    # 1. 提取文本
    pages = extract_text_pdfplumber(pdf_path)
    total_chars = sum(len(p["text"]) for p in pages)
    logger.info(f"[PDF] 文本提取完成，{len(pages)} 页，{total_chars} 字符")
    
    if total_chars < 100 and use_miner_fallback:
        logger.warning("[PDF] pdfplumber 结果过少，切换至 pdfminer")
        pages = extract_text_pdfminer(pdf_path)
        total_chars = sum(len(p["text"]) for p in pages)
    
    # 2. 提取表格
    tables = []
    if extract_tables:
        config = get_config()
        use_camelot = config.get("pdf.use_camelot", True)
        flavor = config.get("pdf.camelot_flavor", "lattice")
        min_accuracy = config.get("pdf.table_min_accuracy", 80)
        
        if use_camelot:
            try:
                tables = extract_tables_camelot(pdf_path, flavor=flavor)
                # 检查准确度
                low_accuracy = [t for t in tables if t["accuracy"] < min_accuracy]
                if low_accuracy:
                    logger.warning(f"[PDF] {len(low_accuracy)} 个表格准确度低于 {min_accuracy}%")
            except Exception as e:
                logger.warning(f"[PDF] Camelot 提取失败: {e}")
                if config.get("pdf.fallback_to_pdfplumber", True):
                    tables = extract_tables_pdfplumber(pdf_path)
        
        logger.info(f"[PDF] 表格提取完成，{len(tables)} 个表格")
    
    # 3. 转换为 Markdown
    md = to_structured_markdown(pages)
    
    # 4. 追加表格
    if tables:
        tables_md = _tables_to_markdown(tables)
        md += tables_md
    
    return md, pages, tables
```

#### 5.3 数据结构变更

```python
# 原返回值
(md: str, pages: list[dict])

# 新返回值
(md: str, pages: list[dict], tables: list[dict])

# tables 结构示例
[
    {
        "page_num": 15,
        "table_index": 0,
        "table": [
            ["字段名", "类型", "描述"],  # 表头
            ["id", "int", "标识符"],      # 行 1
            ["name", "string", "名称"],   # 行 2
        ],
        "accuracy": 95.2,
        "bbox": (50, 100, 500, 300),
    },
    ...
]
```

### 自测清单

| 测试项 | 验证方法 | 预期结果 |
|--------|----------|----------|
| camelot 导入 | `import camelot` | 无报错 |
| 表格提取 | 运行测试脚本 | 提取到表格，accuracy > 80 |
| Markdown 转换 | 检查输出 | 表格格式正确 |
| 低准确度告警 | 模拟低准确度表格 | 日志有告警 |
| 降级回退 | 卸载 camelot 后测试 | 使用 pdfplumber，无报错 |

### 测试用例

```python
# tests/test_parser_pdf.py 新增

class TestCamelotTableExtraction:
    def test_extract_tables_from_sample_pdf(self):
        """测试从样本 PDF 提取表格"""
        md, pages, tables = parse_pdf("tests/fixtures/sample_table.pdf")
        assert len(tables) > 0
        assert tables[0]["accuracy"] > 80
        assert "| 字段名 |" in md  # Markdown 表格格式
    
    def test_table_to_markdown(self):
        """测试表格转 Markdown"""
        table_data = [
            ["字段", "类型"],
            ["id", "int"],
        ]
        md = _table_to_markdown(table_data, "测试表格")
        assert "| 字段 | 类型 |" in md
        assert "|---|" in md
        assert "| id | int |" in md
    
    def test_fallback_to_pdfplumber(self, monkeypatch):
        """测试降级到 pdfplumber"""
        # 模拟 camelot 导入失败
        # ...
```

---

## 六、Phase 2：上下文保留（表格插回章节）

### 目标
将表格插回其所在章节末尾，保留上下文关联。

### 核心问题

**如何确定表格属于哪个章节？**

| 信息 | 来源 | 可靠性 |
|------|------|--------|
| 表格所在页码 | camelot 提供 | 100% |
| 章节所在页码 | `<!-- page=N -->` 注释 | 100% |
| 表格首行文本 | camelot 提供 | 中等 |
| 表格前后文本 | 需额外提取 | 高（但实现复杂） |

**策略**: 优先用页码关联，必要时用文本匹配辅助。

### 设计

#### 6.1 数据结构扩展

```python
# 表格新增字段
{
    "page_num": 15,
    "table_index": 0,
    "table": [...],
    "accuracy": 95.2,
    "bbox": (50, 100, 500, 300),
    
    # 新增字段（Phase 2）
    "section_id": "4.2.1",           # 所属章节编号
    "section_title": "Message Format",  # 所属章节标题
    "context_before": "...",         # 表格前的文本（用于验证）
}
```

#### 6.2 章节-表格关联算法

```python
def _associate_tables_with_sections(
    md: str,
    tables: list[dict],
    pages: list[dict],
) -> list[dict]:
    """
    将表格关联到所属章节。
    
    算法：
    1. 解析 Markdown 得到章节列表（含页码范围）
    2. 对每个表格，根据页码找到候选章节
    3. 如果表格跨多页，取起始页所在章节
    4. 如果同页有多个章节，用文本匹配辅助定位
    """
    # 1. 解析章节结构
    sections = _parse_sections_from_markdown(md)
    # sections = [
    #     {"id": "4.2.1", "title": "Message Format", "start_page": 15, "end_page": 17, "start_line": 120},
    #     ...
    # ]
    
    # 2. 关联表格
    for tbl in tables:
        page = tbl["page_num"]
        
        # 找到包含该页的章节
        matched_sections = [
            s for s in sections
            if s["start_page"] <= page <= s["end_page"]
        ]
        
        if len(matched_sections) == 1:
            # 唯一匹配
            tbl["section_id"] = matched_sections[0]["id"]
            tbl["section_title"] = matched_sections[0]["title"]
        elif len(matched_sections) > 1:
            # 同页多个章节，用文本匹配辅助
            best_match = _find_best_section_by_text(
                tbl, matched_sections, pages[page - 1]["text"]
            )
            tbl["section_id"] = best_match["id"]
            tbl["section_title"] = best_match["title"]
        else:
            # 未匹配（表格在章节外，如附录）
            tbl["section_id"] = None
            tbl["section_title"] = f"未分类（P{page}）"
    
    return tables


def _parse_sections_from_markdown(md: str) -> list[dict]:
    """
    从 Markdown 解析章节结构。
    
    识别 # / ## / ### 标题，提取：
    - 章节编号
    - 章节标题
    - 起始页码（从 <!-- page=N --> 注释）
    - 起始行号
    """
    import re
    
    sections = []
    lines = md.split("\n")
    current_section = None
    current_page = 1
    
    page_pattern = re.compile(r'<!-- page=(\d+) -->')
    heading_pattern = re.compile(r'^(#{1,4})\s+(.+)$')
    
    for i, line in enumerate(lines):
        # 检测页码注释
        page_match = page_pattern.search(line)
        if page_match:
            current_page = int(page_match.group(1))
        
        # 检测章节标题
        heading_match = heading_pattern.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            
            # 解析章节编号（如 4.2.1）
            section_id = _extract_section_id(title)
            
            # 关闭上一个章节
            if current_section:
                current_section["end_line"] = i - 1
                current_section["end_page"] = current_page
            
            # 开启新章节
            current_section = {
                "id": section_id,
                "title": title,
                "start_page": current_page,
                "start_line": i,
                "end_page": current_page,
                "end_line": i,
            }
            sections.append(current_section)
    
    # 最后一个章节的结束位置
    if current_section:
        current_section["end_line"] = len(lines) - 1
        current_section["end_page"] = current_page
    
    return sections


def _extract_section_id(title: str) -> str:
    """从标题中提取章节编号（如 '4.2.1 Message Format' → '4.2.1'）"""
    import re
    m = re.match(r'^(\d+(?:\.\d+)*)', title)
    return m.group(1) if m else title


def _find_best_section_by_text(
    tbl: dict,
    candidates: list[dict],
    page_text: str,
) -> dict:
    """
    当同页有多个章节时，用文本匹配找到最可能的章节。
    
    策略：计算表格首行与各章节内容的相似度。
    """
    # 简化实现：返回第一个候选
    # 实际可用 TF-IDF 或 embedding
    return candidates[0]
```

#### 6.3 表格插回 Markdown

```python
def _insert_tables_into_sections(
    md: str,
    tables: list[dict],
) -> str:
    """
    将表格插回所属章节末尾。
    
    插入位置：章节最后一个段落之后
    标记：<!-- TABLES -->
    """
    if not tables:
        return md
    
    lines = md.split("\n")
    insertions = {}  # {line_index: [table_md, ...]}
    
    # 1. 找到每个章节的结束行
    sections = _parse_sections_from_markdown(md)
    section_end_lines = {s["id"]: s["end_line"] for s in sections}
    
    # 2. 按章节分组表格
    from collections import defaultdict
    tables_by_section = defaultdict(list)
    for tbl in tables:
        if tbl.get("section_id"):
            tables_by_section[tbl["section_id"]].append(tbl)
    
    # 3. 构造插入内容
    for section_id, section_tables in tables_by_section.items():
        if section_id not in section_end_lines:
            continue
        
        end_line = section_end_lines[section_id]
        table_md_parts = ["\n\n<!-- TABLES -->\n"]
        
        for tbl in section_tables:
            caption = f"表格 {tbl['table_index']+1}（P{tbl['page_num']}）"
            table_md = _table_to_markdown(tbl["table"], caption)
            table_md_parts.append(table_md)
            table_md_parts.append(f"<!-- table_page={tbl['page_num']} table_index={tbl['table_index']} -->\n")
        
        insertions[end_line] = table_md_parts
    
    # 4. 执行插入（从后向前，避免行号偏移）
    for line_idx in sorted(insertions.keys(), reverse=True):
        lines[line_idx:line_idx] = insertions[line_idx]
    
    return "\n".join(lines)
```

#### 6.4 主函数更新

```python
def parse_pdf(
    pdf_path: str,
    *,
    use_miner_fallback: bool = True,
    extract_tables: bool = True,
    insert_tables_to_sections: bool = True,  # 新增参数
) -> tuple[str, list[dict], list[dict]]:
    """
    解析 PDF 文档。
    """
    # ... Phase 1 代码 ...
    
    # 4. 处理表格
    if extract_tables and tables:
        # 4.1 关联章节
        tables = _associate_tables_with_sections(md, tables, pages)
        
        # 4.2 插回章节 OR 追加到末尾
        if insert_tables_to_sections:
            md = _insert_tables_into_sections(md, tables)
        else:
            md += _tables_to_markdown(tables)
    
    return md, pages, tables
```

### 自测清单

| 测试项 | 验证方法 | 预期结果 |
|--------|----------|----------|
| 章节解析 | 打印 sections 列表 | 页码范围正确 |
| 单章节表格关联 | 表格在同页唯一章节 | section_id 正确 |
| 多章节表格关联 | 表格在同页多章节 | 匹配到最相关章节 |
| 表格插回位置 | 检查 Markdown | 表格在章节末尾 |
| 跨页表格处理 | 表格跨 2 页 | 关联到起始页章节 |
| 未分类表格 | 表格在章节外 | 标记"未分类" |

### 测试用例

```python
# tests/test_parser_pdf.py 新增

class TestTableSectionAssociation:
    def test_parse_sections_from_markdown(self):
        """测试章节解析"""
        md = "# 4.2 Message\n\n<!-- page=15 -->\nContent...\n\n# 4.3 Data\n\n<!-- page=17 -->\n..."
        sections = _parse_sections_from_markdown(md)
        assert len(sections) == 2
        assert sections[0]["id"] == "4.2"
        assert sections[0]["start_page"] == 15
    
    def test_associate_single_section_table(self):
        """测试单章节表格关联"""
        # ... 测试代码
    
    def test_insert_tables_to_markdown(self):
        """测试表格插回"""
        # ... 测试代码
```

---

## 七、Phase 3：集成测试与文档更新

### 目标
全量测试通过，更新文档，验证实际效果。

### 任务清单

| 任务 | 内容 | 验证 |
|------|------|------|
| 全量测试 | pytest tests/ -m "not slow" | 144+ 通过 |
| 比对测试 | 运行 main.py 对比 O-RAN vs ASTRI | 无报错 |
| 表格验证 | 人工检查 5 个表格 | 结构正确、章节正确 |
| 性能测试 | 测量表提取耗时 | < 10s/100 页 |
| 文档更新 | README / DESIGN / CHANGELOG | 同步更新 |

### 验证对比

**对比维度**：

| 维度 | Phase 1 前 | Phase 2 后 | 验证方法 |
|------|-----------|-----------|----------|
| 表格结构 | 丢失（逐行文本） | 保留 | 检查 Markdown |
| 上下文关联 | 无 | 有 | 检查章节归属 |
| LLM 分析质量 | 低（无法识别表格） | 中/高（结构化输入） | 抽样验证 |
| 差异检测准确性 | 低（表格差异漏检） | 中/高 | 对比 diff 结果 |

**抽样验证方法**：
1. 随机抽取 5 个表格
2. 检查 Markdown 格式是否正确
3. 检查章节归属是否准确
4. 检查差异检测是否识别到表格变更

---

## 八、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Java 环境缺失 | 中 | Phase 0 阻塞 | 提供 pdfplumber 降级 |
| camelot 对某些 PDF 不兼容 | 低 | Phase 1 失败 | try/except 捕获，降级 |
| 表格章节关联错误 | 中 | 上下文丢失 | 人工抽样验证 + 修正算法 |
| 性能下降（表格提取耗时） | 低 | 比对变慢 | 异步提取 / 缓存 |
| 测试 PDF 样本不足 | 低 | 验证不充分 | 使用真实 O-RAN 文档 |

---

## 九、时间规划

| 阶段 | 预计时间 | 累计 | 里程碑 |
|------|---------|------|--------|
| Phase 0 | 0.5h | 0.5h | camelot 可用 |
| Phase 1 | 2-3h | 3.5h | 表格提取 + Markdown |
| Phase 2 | 3-5h | 8.5h | 表格插回章节 |
| Phase 3 | 1-2h | 10.5h | 全量测试通过 |

**总计**: 约 10-11 小时（分 2-3 个工作日完成）

---

## 十、交付物清单

| 交付物 | 阶段 | 说明 |
|--------|------|------|
| `config/settings.yml.template` | Phase 1 | 新增 pdf 配置项 |
| `src/parser_pdf.py` | Phase 1+2 | 新增表格提取函数 |
| `tests/test_parser_pdf.py` | Phase 1+2 | 新增测试用例 |
| `requirements.txt` | Phase 0 | 新增 camelot-py[cv] |
| `README.md` | Phase 3 | 更新表格处理说明 |
| `DESIGN.md` | Phase 3 | 更新架构说明 |
| `CHANGELOG.md` | Phase 3 | 记录变更 |

---

## 十一、确认事项

请确认以下几点：

1. ✅ **方案 3（camelot）** 是否认可？
2. ✅ **分阶段实施**（Phase 0→1→2→3）是否可行？
3. ✅ **上下文保留策略**（页码关联 + 文本匹配辅助）是否合理？
4. ✅ **时间规划**（10-11h）是否可接受？
5. 是否有其他约束或要求？

---

**状态**: 待用户确认后开始 Phase 0
