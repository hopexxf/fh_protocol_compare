"""
analyzer 健壮性回归测试。

覆盖范围（对应 2026-08-01 比对失败根因）：
  TC-A  Gateway 端口动态发现：必须读 openclaw.json 实时端口，而非静态配置
  TC-B  异常日志非静默：httpcore ReadTimeout.str() 为空，必须记录异常类型名
  TC-C  llm_client=None 时正确降级（不抛 AttributeError）
  TC-E  Base 独有章节走 LLM（不硬编码 feature_removed）
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
    _truncate,
    MAX_LLM_CONTENT_CHARS,
    _build_messages,
    _extract_content_from_response,
    _fallback_result,
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
    """模拟 httpx 非流式响应：.text 为 SSE 格式字符串（与 Gateway 实际返回一致），
    analyzer 的 _extract_content_from_response 兼容解析。
    """
    def __init__(self, text):
        self.text = text
        self.status_code = 200
        self.headers = {}

    def raise_for_status(self):
        return None


class _FakeClient:
    """可配置成功/失败的假 httpx.AsyncClient（非流式 post，匹配 stream:False）。

    _fail_first_n: 前 N 个 item（按 index）的所有 attempt 均失败（模拟网络抖动）；
    其余 item 成功。基于 payload 中的 item index 判定，与并发/重试调度无关。
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

    async def post(self, *args, **kwargs):
        payload = kwargs.get("json") or (args[1] if len(args) >= 2 else None)
        idx = self._extract_index(payload)
        if idx is not None and idx < _FakeClient._fail_first_n:
            # 前 N 个 item 的所有 attempt 均失败，模拟连接失败
            raise httpx.ConnectError("connection refused (simulated)")
        return _FakeResp(_ok_sse_payload())


# ---------------------------------------------------------------------------
# TC-A: Gateway 端口动态发现
# ---------------------------------------------------------------------------

def test_get_gateway_port_reads_openclaw_json(tmp_path, monkeypatch):
    """_get_gateway_port 必须优先读 openclaw.json 实时端口。"""
    from src.llm_client import _reset_gateway_port_cache
    _reset_gateway_port_cache()  # 确保测试隔离
    cfg = {"gateway": {"port": 57780, "auth": {"token": "abc"}}}
    qclaw_dir = tmp_path / ".qclaw"
    qclaw_dir.mkdir()
    (qclaw_dir / "openclaw.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr("src.llm_client.Path.home", lambda: tmp_path)
    # 隔离 settings.yml 读取，避免真实配置的 gateway_port 覆盖 mock 值
    import src.config_loader as cl
    monkeypatch.setattr(cl, "get_config", lambda: {})
    # step1 openclaw.json 直接返回，不走 step2/settings/fallback
    assert _get_gateway_port() == 57780


def test_get_gateway_port_fallback_settings(tmp_path, monkeypatch):
    """openclaw.json 缺失时回退 settings.yml 的 gateway_port。"""
    from src.llm_client import _reset_gateway_port_cache
    _reset_gateway_port_cache()  # 确保测试隔离
    monkeypatch.setattr("src.llm_client.Path.home", lambda: tmp_path)  # 无 openclaw.json
    import src.config_loader as cl
    monkeypatch.setattr(cl, "get_config", lambda: {"llm": {"gateway_port": 12345}})
    assert _get_gateway_port() == 12345


def test_get_gateway_port_default(tmp_path, monkeypatch):
    """openclaw.json 与 settings.yml 均缺失时回退硬编码默认值 60772。"""
    from src.llm_client import _reset_gateway_port_cache
    _reset_gateway_port_cache()  # 确保测试隔离
    monkeypatch.setattr("src.llm_client.Path.home", lambda: tmp_path)
    import src.config_loader as cl
    monkeypatch.setattr(cl, "get_config", lambda: {})
    assert _get_gateway_port() == 60772


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
    """20 个 item 中前 3 个网络持续失败（所有 attempt），其余 17 个应正常产出，
    失败项 summary 非空且含异常类型，且不影响其他任务。

    注意：失败数（3）须小于熔断阈值 CONSECUTIVE_FAIL_LIMIT（5），
    否则连续失败会触发熔断、后续 item 不再重试直接兜底（见 test_batch_circuit_breaker_all_fail）。
    """
    results = _run_batch(_make_items(20), fail_first_n=3, concurrency=5)
    # 失败项（LLM 异常兜底，summary 含 "LLM 调用失败"）；成功项 summary 为 LLM 原始输出
    failed = [r for r in results if "LLM 调用失败" in r["llm_result"].get("summary", "")]
    success = [r for r in results if r not in failed]
    assert len(success) == 17, f"成功数应为 17，实际 {len(success)}"
    assert len(failed) == 3, f"失败数应为 3，实际 {len(failed)}"
    for r in failed:
        s = r["llm_result"]["summary"]
        assert s != "LLM 调用失败: ", "失败 summary 不应为空（静默失败）"
        assert "ConnectError" in s, f"失败 summary 应含异常类型名: {s!r}"


def test_batch_circuit_breaker_all_fail():
    """断网场景（全部失败）：连续失败达到阈值后熔断，剩余 item 不再重试直接兜底，
    避免 623 项 × 150s × 3 次重试的无意义空转。
    """
    results = _run_batch(_make_items(20), fail_first_n=20, concurrency=5)
    assert len(results) == 20
    summaries = [r["llm_result"].get("summary", "") for r in results]
    # 熔断前失败项含异常类型；熔断后跳过项标记熔断开启
    assert all("LLM 调用失败" in s or "熔断" in s for s in summaries)
    assert any("熔断开启" in s for s in summaries), "应存在熔断后跳过的 item"


def test_batch_circuit_breaker_all_fail():
    """断网场景（全部失败）：连续失败达到阈值后熔断，剩余 item 不再重试直接兜底，
    避免 623 项 × 150s × 3 次重试的无意义空转。
    """
    results = _run_batch(_make_items(20), fail_first_n=20, concurrency=5)
    assert len(results) == 20
    summaries = [r["llm_result"].get("summary", "") for r in results]
    # 熔断前失败项含异常类型；熔断后跳过项标记熔断开启
    assert all("LLM 调用失败" in s or "熔断" in s for s in summaries)
    assert any("熔断开启" in s for s in summaries), "应存在熔断后跳过的 item"


def test_batch_circuit_breaker_skips_http_after_open():
    """
    熔断开启后，剩余 item 不再发起 HTTP 调用，秒级完成。

    场景：concurrency=5，阈值=10。前 10 个 item 的 HTTP 调用均失败（模拟断网），
    触发熔断开启。后续 10 个 item 必须不发起 HTTP，直接走兜底。

    回归：修复前熔断检查在 semaphore.acquire() 之前，等在信号量上的协程
    拿到 slot 后直接发 HTTP，仍等 30s 超时，导致熔断后每条仍耗时 30s。
    """
    _FakeClient._fail_first_n = 100          # 前 100 个 item 全部失败
    _FakeClient._call_count = 0              # 重置计数器

    mock_client = _StubClient()
    import src.analyzer as analyzer_mod
    import asyncio as _asyncio
    _orig_sleep = _asyncio.sleep

    # 自定义 FakeClient 统计 HTTP 调用次数
    class _CountingClient:
        http_calls = 0

        class _Resp:
            text = ""
            status_code = 200
            def raise_for_status(self): return None

        def __init__(self, *args, **kwargs): pass

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, *args, **kwargs):
            _CountingClient.http_calls += 1
            raise httpx.ConnectError("simulated")

    fake_httpx = SimpleNamespace(AsyncClient=_CountingClient, Timeout=httpx.Timeout)
    with patch.object(analyzer_mod, "httpx", fake_httpx), \
         patch.object(analyzer_mod, "_load_gateway_token", lambda: "fake-token"), \
         patch.object(analyzer_mod, "_get_gateway_port", lambda: 1), \
         patch.object(_asyncio, "sleep", lambda *a, **k: _orig_sleep(0)):
        import time
        t0 = time.perf_counter()
        results = analyze_diff_batch(
            _make_items(20), llm_client=mock_client, concurrency=5
        )
        elapsed = time.perf_counter() - t0

    assert len(results) == 20, f"应返回 20 个结果，实际 {len(results)}"
    # 熔断开启后不应再有 HTTP 调用（阈值=10，前 10 个触发后剩余 10 个直接兜底）
    # 前 10 个每个有 1 个 HTTP 调用（fail_first_n=100，第 1 次 attempt 即失败）
    assert _CountingClient.http_calls <= 10, (
        f"熔断开启后不应发起 HTTP 调用，前 10 个最多 10 次，"
        f"实际 {_CountingClient.http_calls} 次"
    )
    # 熔断后剩余 10 个应秒级完成（无 sleep、无等待）
    assert elapsed < 1.0, (
        f"熔断后 10 条应秒级完成（<1s），实际 {elapsed:.2f}s，"
        f"可能是熔断后仍在等待 semaphore 或发起 HTTP"
    )
    # 熔断前项含失败原因，熔断后项含熔断标记
    summaries = [r["llm_result"].get("summary", "") for r in results]
    assert any("熔断开启" in s for s in summaries), "应有熔断后跳过的 item"

    # 清理
    _FakeClient._fail_first_n = 0
    _FakeClient._call_count = 0


def test_fallback_base_only_feature_removed():
    """Base 独有章节在 LLM 不可用时兜底为 feature_removed（与 analyze_diff_item 一致），
    保证报告流程可继续。"""
    results = _run_batch(_make_items(3, base_only=True), fail_first_n=3, concurrency=3)
    assert len(results) == 3
    for r in results:
        diffs = r["llm_result"].get("diffs", [])
        assert diffs, "Base 独有章节兜底应有 feature_removed diff"
        assert diffs[0]["type"] == "feature_removed", "兜底类型应为 feature_removed"


def _make_items_compare_only(n):
    """Compare 独有章节（base_section_id=None，compare_section_id 有值）。"""
    items = []
    for i in range(n):
        items.append({
            "base_section_id": None,
            "compare_section_id": f"c{i}",
            "has_diff": True,
            "base_content": "",
            "compare_content": f"compare {i}",
        })
    return items


def test_fallback_compare_only_feature_added():
    """Compare 独有章节在 LLM 不可用时兜底为 feature_added（任务 1）。"""
    item = {
        "base_section_id": None,
        "compare_section_id": "c0",
        "base_content": "",
        "compare_content": "compare 0",
    }
    r = _fallback_result(item, "ConnectError: simulated")
    diffs = r["llm_result"]["diffs"]
    assert diffs, "Compare 独有章节兜底应有 feature_added diff"
    assert diffs[0]["type"] == "feature_added", "兜底类型应为 feature_added"


def test_fallback_aligned_unknow_diff():
    """对齐章节在 LLM 异常时兜底为 unknow_diff（任务 3：仅 LLM 异常的对齐章节）。

    回归：修复前对齐章节兜底为空 diffs，被 reporter 的 `if not diffs: continue` 丢弃，
    导致这类章节在断网报告中完全消失。
    """
    item = {
        "base_section_id": "b0",
        "compare_section_id": "c0",
        "base_content": "base 0",
        "compare_content": "compare 0",
    }
    r = _fallback_result(item, "熔断开启")
    diffs = r["llm_result"]["diffs"]
    assert diffs, "对齐章节 LLM 异常兜底不应为空（否则被 reporter 丢弃）"
    assert diffs[0]["type"] == "unknow_diff", "对齐章节 LLM 异常兜底类型应为 unknow_diff"


def test_batch_base_only_goes_to_llm():
    """Base 独有章节（compare_section_id=None）应进入 LLM 分析，而非硬编码 feature_removed。"""
    items = _make_items(3, base_only=True)
    results = _run_batch(items, fail_first_n=0)
    assert len(results) == 3
    for r in results:
        # 走 LLM 路径 -> 有 diffs；若被硬编码 feature_removed 则 llm_result 无 diffs
        assert r["llm_result"]["diffs"], "Base 独有章节应经 LLM 分析，不应硬编码 feature_removed"


def test_batch_no_diff_skipped():
    """无差异的对齐章节应跳过（summary=无显著变更），不调用 LLM。"""
    items = [{
        "base_section_id": "b0", "compare_section_id": "c0",
        "has_diff": False, "base_content": "x", "compare_content": "x",
    }]
    results = _run_batch(items, fail_first_n=0)
    assert results[0]["llm_result"]["summary"] == "无显著变更"


# ---------------------------------------------------------------------------
# TC-F / TC-H: 纯函数回归（无需真实 Gateway 网络）
# ---------------------------------------------------------------------------

def test_extract_content_from_response_json():
    """Gateway 偶发返回纯 JSON（非 SSE），必须能解析。"""
    text = json.dumps({"choices": [{"message": {"content": "HELLO"}}]})
    assert _extract_content_from_response(text) == "HELLO"


def test_extract_content_from_response_sse():
    """Gateway 偶发返回 SSE（data: 行），必须能解析。"""
    chunk = {"choices": [{"delta": {"content": "WORLD"}}]}
    text = "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n"
    assert _extract_content_from_response(text) == "WORLD"


def test_extract_content_from_response_empty():
    assert _extract_content_from_response("") == ""
    assert _extract_content_from_response("   ") == ""


def test_truncate_caps_long_content():
    """超长内容必须截断，防止 Gateway 对 >~8K 字符请求挂死。"""
    long = "X" * 5000
    out = _truncate(long)
    assert "X" * 5000 not in out
    assert "内容已截断" in out
    assert "保留前 %d 字符" % MAX_LLM_CONTENT_CHARS in out


def test_truncate_keeps_short_content():
    assert _truncate("短文本") == "短文本"
    assert _truncate("") == ""


def test_build_messages_truncates_content():
    """_build_messages 注入 prompt 前必须截断超长内容。"""
    long = "Y" * 5000
    item = {
        "base_section_id": "b1", "compare_section_id": "c1",
        "base_section_number": "1", "compare_section_number": "1",
        "base_section_title": "T1", "compare_section_title": "T2",
        "base_content": long, "compare_content": long,
        "diff_summary": "x",
    }
    msgs = _build_messages(item)
    joined = "\n".join(m["content"] for m in msgs)
    assert "Y" * 5000 not in joined
