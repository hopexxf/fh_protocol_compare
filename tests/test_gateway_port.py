"""
Gateway 端口发现测试。

从 ~/.qclaw/openclaw.json 读取当前 gateway 配置，
自动检测可用端口（19000/53311），验证 /v1/chat/completions 端点。

使用方法：
    py -3 -m pytest tests/test_gateway_port.py -v
"""

import json
from pathlib import Path

import httpx
import pytest

from src.llm_client import _load_gateway_token


def _get_gateway_config():
    """从 openclaw.json 读取 gateway 配置"""
    cfg_path = Path.home() / ".qclaw" / "openclaw.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    gw = cfg.get("gateway", {})
    return {
        "port": gw.get("port", 53311),
        "token": gw.get("auth", {}).get("token", ""),
    }


def _discover_active_port():
    """探测所有可能的 gateway 端口，返回第一个可达的 (port, token)"""
    cfg = _get_gateway_config()
    token = cfg["token"]
    configured_port = cfg["port"]
    candidates = [configured_port, 19000, 61791, 53301]

    # 去重
    seen = set()
    candidates = [p for p in candidates if not (p in seen or seen.add(p))]

    for port in candidates:
        url = f"http://localhost:{port}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"model": "openclaw", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5, "stream": False, "user": "test_gateway_port"}
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=30.0)
            if resp.status_code == 200:
                return port, token
        except Exception:
            pass
    return None, token


class TestGatewayDiscovery:
    """Gateway 端口自动发现"""

    def test_discover_active_port(self):
        """验证能自动发现可用的 gateway 端口"""
        port, token = _discover_active_port()
        assert port is not None, "未找到可用的 Gateway 端口"
        assert len(token) > 0, "Gateway token 为空"
        print(f"\n  发现可用端口: {port}")

    def test_chat_completions(self):
        """验证 /v1/chat/completions 端点可正常响应"""
        port, token = _discover_active_port()
        if port is None:
            pytest.skip("无可用 Gateway 端口")

        url = f"http://localhost:{port}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {
            "model": "openclaw",
            "messages": [{"role": "user", "content": "reply OK only"}],
            "max_tokens": 5,
            "stream": False,
            "user": "test_gateway_port",
        }
        resp = httpx.post(url, headers=headers, json=payload, timeout=30.0)
        assert resp.status_code == 200, f"请求失败: {resp.status_code} {resp.text}"

        data = resp.json()
        assert "choices" in data, f"响应格式错误: {data}"
        content = data["choices"][0]["message"]["content"]
        print(f"\n  响应: {content}")

    def test_load_gateway_token_reads_openclaw_json(self, tmp_path, monkeypatch):
        """_load_gateway_token 必须动态读 openclaw.json 的 token（非静态写死）。"""
        cfg = {"gateway": {"auth": {"token": "SECRET_TOKEN_XYZ"}}}
        qclaw = tmp_path / ".qclaw"
        qclaw.mkdir()
        (qclaw / "openclaw.json").write_text(json.dumps(cfg), encoding="utf-8")
        monkeypatch.setattr("src.llm_client.Path.home", lambda: tmp_path)
        assert _load_gateway_token() == "SECRET_TOKEN_XYZ"

    def test_load_gateway_token_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.llm_client.Path.home", lambda: tmp_path)  # 无 .qclaw 目录
        assert _load_gateway_token() is None


if __name__ == "__main__":
    port, token = _discover_active_port()
    if port:
        print(f"Gateway 端口: {port} ✅")
    else:
        print("未找到可用 Gateway 端口 ❌")
