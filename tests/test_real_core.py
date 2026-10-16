import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml

from proxy_manager.core import Core, inventory
from proxy_manager.service import Manager
from proxy_manager.store import Store

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not (ROOT / "bin" / "mihomo.exe").exists(), reason="需要已安装的 Windows Mihomo")
def test_real_mihomo_refresh_filter_select_proxy_and_reject(tmp_path, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            self.send_response(200)
            self.end_headers()
            self.close_connection = False

        def do_GET(self):
            if self.path == "/feed":
                port = self.server.server_port
                body = yaml.safe_dump({"proxies":[
                    {"name":name, "type":"http", "server":"127.0.0.1", "port":port}
                    for name in ["日本JP-A", "香港HK-A", "美国US-A"]
                ]}, allow_unicode=True).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(204)
                self.end_headers()

        do_HEAD = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("PROXY_MANAGER_CORE_PORT", str(free_port()))
    proxy_port = free_port()

    async def scenario():
        store = Store(tmp_path)
        store.state["settings"]["proxy_port"] = proxy_port
        store.state["settings"]["test_url"] = "http://probe.invalid/check"
        core = Core(tmp_path, store.state["controller_secret"], ROOT)
        manager = Manager(store, core)
        try:
            await manager.start()
            assert core.running, manager.error
            await manager.save_subscription({"name":"真实内核测试", "url":f"http://127.0.0.1:{server.server_port}/feed", "enabled":True})
            results = await manager.refresh()
            assert results[0]["ok"], results
            assert len(inventory(store.state)) == 3
            await manager.save_settings(store.state["settings"] | {"excluded_regions":["HK"]})
            nodes = inventory(store.state)
            assert [n["region"] for n in nodes if n["eligible"]] == ["JP", "US"]
            us = next(n for n in nodes if n["region"] == "US")
            await manager.select(us["id"])
            current = (await core.request("GET", "/proxies/OUTBOUND"))["now"]
            assert current == us["core_name"]
            async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{proxy_port}", trust_env=False) as client:
                response = await client.get("http://probe.invalid/request", timeout=5)
                assert response.status_code == 204
            await manager.start_tests(None)
            await manager.test_task
            status = await manager.status()
            assert len([n for n in status["nodes"] if n["status"] == "available"]) == 2
            # A bad reload must leave the running exit and the durable state intact.
            invalid = store.snapshot()
            invalid["subscriptions"][0]["nodes"][0]["type"] = "invalid-test-protocol"
            with pytest.raises(ValueError):
                await manager.commit(invalid)
            assert store.state["subscriptions"][0]["nodes"][0]["type"] == "http"
            assert (await core.request("GET", "/proxies/OUTBOUND"))["now"] == us["core_name"]
            # Proxy authentication applies to the shared application-facing entry.
            await manager.save_settings(store.state["settings"] | {
                "proxy_username":"route-test", "proxy_password":"route-test-proxy-password"})
            async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{proxy_port}", trust_env=False) as client:
                assert (await client.get("http://probe.invalid/no-auth", timeout=5)).status_code == 407
            authenticated_proxy = f"http://route-test:route-test-proxy-password@127.0.0.1:{proxy_port}"
            async with httpx.AsyncClient(proxy=authenticated_proxy, trust_env=False) as client:
                assert (await client.get("http://probe.invalid/authenticated", timeout=5)).status_code == 204
            await manager.save_settings(store.state["settings"] | {"excluded_regions":["HK", "JP", "US"]})
            assert store.state["selected"] == "AUTO"
            async with httpx.AsyncClient(proxy=authenticated_proxy, trust_env=False) as client:
                try:
                    response = await client.get("http://probe.invalid/must-reject", timeout=5)
                    assert response.status_code != 204
                except httpx.HTTPError:
                    pass
            assert "真实内核测试" in json.dumps(Store(tmp_path).state, ensure_ascii=False)
        finally:
            await manager.close()

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
