"""
analyzer 健壮性回归测试。

覆盖范围（对应 2026-08-01 比对失败根因）：
  TC-A  Gateway 端口动态发现：必须读 openclaw.json 实时端口，而非静态配置
  TC-B  异常日志非静默：httpcore ReadTimeout.str() 为空，必须记录异常类型名
  TC-C  llm_client=None 时正确降级（不抛 AttributeError）
  TC-E  Base 独有章节走 LLM（不硬编码 feature-removed）
  TC-G  单任务失败不影响其他（gather 韧性 + 失败 summary 非空）

所有测试均不依赖真实 Gateway 网络：通过 monkeypatch httpx.AsyncClient 注入假的 SSE 流。
"""

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.analyzer import (
    analyze_diff_batch,
    analyze_diff_batch_async,
    _fmt_err,
)
from src.llm_client import _get_gateway_port


# ---------------------------------------------------------------------------
# 假的 SSE 流式响应（避免真实网络）
# ---------------------------------------------------------------------------

def _ok_sse_payload(diffs=None, summary="测试摘要"):
    """构造一条合法的 LLM 流式响应（OpenAI SSE 格式）。

    analyzer 解析的是 OpenAI 流式格式：choices[0].delta.content 累积为 JSON，
    再经 _parse_llm_result 解析为 {diffs, summary}。
    """
    if diffs is None:
        diffs = [{
            "type": "param-diff", "impact": "中",
            "base": "base text", "compare": "compare text",
            "desc": "差异说明", "workload": "小",
        }]
    body = {"diffs": diffs, "summary": summary}
    content = json.dumps(body, ensure_ascii=False)
    chunk = {"choices": [{"delta": {"content": content}}]}
    return "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\ndata: [DONE]\n"


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200
        self.headers = {}

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    """可配置成功/失败的假 httpx.AsyncClient。

    _fail_first_n: 前 N 个 item（按 index）的所有 attempt 均失败（模拟网络抖动）；
    其余 item 成功。基于 payload 中的 item index 判定，与并发/重试调度无关，
    保证恰好 N 个 item 永久失败（验证“单任务失败不影响其他 + 失败 summary 非空”）。
    """

    _fail_first_n = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @staticmethod
    def _extract_index(payload):
        if not payload:
            return None
        for msg in payload.get("messages", []):
            content = msg.get("content", "")
            m = re.search(r"base (\d+)", content)
            if m:
                return int(m.group(1))
        return None

    def stream(self, *args, **kwargs):
        payload = kwargs.get("json") or (args[2] if len(args) >= 3 else None)
        idx = self._extract_index(payload)
        if idx is not None and idx < _FakeClient._fail_first_n:
            # 前 N 个 item 的所有 attempt 均失败，模拟连接失败
            raise httpx.ConnectError("connection refused (simulated)")
        resp = _FakeResp(_ok_sse_payload().splitlines(keepends=True))
        return _FakeStreamCtx(resp)


# ---------------------------------------------------------------------------
# TC-A: Gateway 端口动态发现
# ---------------------------------------------------------------------------

def test_get_gateway_port_reads_openclaw_json(tmp_path, monkeypatch):
    """_get_gateway_port 必须优先读 openclaw.json 实时端口。"""
    cfg = {"gateway": {"port": 57780, "auth": {"token": "abc"}}}
    qclaw_dir = tmp_path / ".qclaw"
    qclaw_dir.mkdir()
    (qclaw_dir / "openclaw.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr("src.llm_client.Path.home", lambda: tmp_path)
    assert _get_gateway_port() == 57780


def test_get_gateway_port_fallback_settings(tmp_path, monkeypatch):
    """openclaw.json 缺失时回退 settings.yml 的 gateway_port。"""
    monkeypatch.setattr("src.llm_client.Path.home", lambda: tmp_path)  # 无 openclaw.json
    import src.config_loader as cl
    monkeypatch.setattr(cl, "get_config", lambda: {"llm": {"gateway_port": 12345}})
    assert _get_gateway_port() == 12345


def test_get_gateway_port_default(tmp_path, monkeypatch):
    """openclaw.json 与 settings.yml 均缺失时回退默认 61791。"""
    monkeypatch.setattr("src.llm_client.Path.home", lambda: tmp_path)
    import src.config_loader as cl
    monkeypatch.setattr(cl, "get_config", lambda: {})
    assert _get_gateway_port() == 61791


# ---------------------------------------------------------------------------
# TC-B: 异常日志非静默
# ---------------------------------------------------------------------------

def test_fmt_err_empty_str_exception_nonempty():
    """httpcore 1.0.9 的 ReadTimeout.str() 为空，_fmt_err 必须返回非空且含类型名。

    注意：httpx 0.28.1 的 httpx.ReadTimeout() 构造要求 message 参数，
    无法直接实例化空 str 的 ReadTimeout；此处用 str() 返回空串的自定义异常
    等价模拟 httpcore.ReadTimeout 的静默行为。
    """
    class _EmptyStrError(Exception):
        def __str__(self):
            return ""

    e = _EmptyStrError()
    msg = _fmt_err(e)
    assert msg, "空 str 异常的描述不应为空（否则失败静默）"
    assert "_EmptyStrError" in msg


def test_fmt_err_normal_exception():
    e = ValueError("boom")
    assert _fmt_err(e) == "ValueError: boom"


# ---------------------------------------------------------------------------
# TC-C / TC-G: 批量分析（mock 网络）
# ---------------------------------------------------------------------------

def _make_items(n, base_only=False):
    items = []
    for i in range(n):
        item = {
            "base_section_id": f"b{i}",
            "compare_section_id": f"c{i}",
            "has_diff": True,
            "base_content": f"base {i}",
            "compare_content": f"compare {i}",
        }
        if base_only:
            item["compare_section_id"] = None
        items.append(item)
    return items


class _StubClient:
    """极简桩 client（非 MagicMock），用于让 analyzer 走异步路径（patch httpx）。

    与 MagicMock 的区别：没有 `called` 属性，因此 _is_mock_client() 判定为非 mock，
    走 httpx.AsyncClient 异步路径（被测试 patch 为 _FakeClient）。
    """
    config = {"llm": {"gateway_port": 1}}

    def set_session_label(self, label):
        pass

    def cleanup_sessions(self):
        return True


def _run_batch(items, fail_first_n=0, concurrency=5):
    """用假网络跑批量分析。

    fail_first_n: 前 N 个 item 的所有 attempt 失败（其余成功）。
    """
    _FakeClient._fail_first_n = fail_first_n
    mock_client = _StubClient()

    import src.analyzer as analyzer_mod
    import asyncio as _asyncio
    _orig_sleep = _asyncio.sleep  # 捕获原始 sleep，避免 patch 自递归
    # 用 unittest.mock.patch 临时替换，避免触及真实网络 / 真实 token
    fake_httpx = SimpleNamespace(AsyncClient=_FakeClient, Timeout=httpx.Timeout)
    with patch.object(analyzer_mod, "httpx", fake_httpx), \
         patch.object(analyzer_mod, "_load_gateway_token", lambda: "fake-token"), \
         patch.object(analyzer_mod, "_get_gateway_port", lambda: 1), \
         patch.object(_asyncio, "sleep", lambda *a, **k: _orig_sleep(0)):
        return analyze_diff_batch(items, llm_client=mock_client, concurrency=concurrency)


def test_batch_all_success():
    """20 个 item 全成功，每个都应有 diffs。"""
    results = _run_batch(_make_items(20), fail_first_n=0)
    assert len(results) == 20
    for r in results:
        assert r["llm_result"]["diffs"], f"应分析成功: {r['base_section_id']}"


def test_batch_partial_failure_resilient():
    """20 个 item 中前 5 个网络持续失败（所有 attempt），其余 15 个应正常产出，
    失败项 summary 非空且含异常类型，且不影响其他任务。
    """
    results = _run_batch(_make_items(20), fail_first_n=5, concurrency=5)
    success = [r for r in results if r["llm_result"].get("diffs")]
    failed = [r for r in results if "LLM 调用失败" in r["llm_result"].get("summary", "")]
    assert len(success) == 15, f"成功数应为 15，实际 {len(success)}"
    assert len(failed) == 5, f"失败数应为 5，实际 {len(failed)}"
    for r in failed:
        s = r["llm_result"]["summary"]
        assert s != "LLM 调用失败: ", "失败 summary 不应为空（静默失败）"
        assert "ConnectError" in s, f"失败 summary 应含异常类型名: {s!r}"


def test_batch_base_only_goes_to_llm():
    """Base 独有章节（compare_section_id=None）应进入 LLM 分析，而非硬编码 feature-removed。"""
    items = _make_items(3, base_only=True)
    results = _run_batch(items, fail_first_n=0)
    assert len(results) == 3
    for r in results:
        # 走 LLM 路径 -> 有 diffs；若被硬编码 feature-removed 则 llm_result 无 diffs
        assert r["llm_result"]["diffs"], "Base 独有章节应经 LLM 分析，不应硬编码 feature-removed"


def test_batch_no_diff_skipped():
    """无差异的对齐章节应跳过（summary=无显著变更），不调用 LLM。"""
    items = [{
        "base_section_id": "b0", "compare_section_id": "c0",
        "has_diff": False, "base_content": "x", "compare_content": "x",
    }]
    results = _run_batch(items, fail_first_n=0)
    assert results[0]["llm_result"]["summary"] == "无显著变更"
