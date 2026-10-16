"""Verify automatic failover against the installed core, without production data."""
import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from proxy_manager.core import Core, inventory
from proxy_manager.service import Manager
from proxy_manager.store import Store

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not (ROOT / "bin" / "mihomo.exe").exists(), reason="需要已安装的 Windows Mihomo")
def test_auto_reselects_after_periodic_probe_failure_and_manual_remains_fixed(tmp_path, monkeypatch):
    class Proxy(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            self.send_response(200 if self.server.healthy else 503)
            if not self.server.healthy:
                self.send_header("Content-Length", "0")
                self.close_connection = True
            self.end_headers()

        def do_GET(self):
            if not self.server.healthy:
                # Existing probe tunnels can be reused; fail those as well as new CONNECTs.
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            time.sleep(self.server.delay)
            self.send_response(204)
            self.end_headers()

        do_HEAD = do_GET

    servers = []
    for delay in (.035, .130):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        server.healthy, server.delay = True, delay
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    monkeypatch.setenv("PROXY_MANAGER_CORE_PORT", str(free_port()))

    async def scenario():
        store = Store(tmp_path)
        # Accelerate only this isolated native scheduler; production settings stay unchanged.
        store.state["settings"].update(proxy_port=free_port(), test_interval=1,
                                       test_url="http://probe.invalid/check")
        store.state["subscriptions"] = [{
            "id":"failover-test", "name":"自动切换测试", "url":"https://example.org/unused",
            "enabled":True, "last_attempt":time.time(), "last_success":None, "error":"", "warnings":[],
            "nodes":[{"name":name, "type":"http", "server":"127.0.0.1", "port":server.server_port}
                     for name, server in zip(("日本快速节点", "新加坡备用节点"), servers)],
        }]
        core = Core(tmp_path, store.state["controller_secret"], ROOT)
        manager = Manager(store, core)
        fast, backup = inventory(store.state)

        async def wait_for_selected(name, failed_name=None):
            deadline = time.monotonic() + 18
            while time.monotonic() < deadline:
                live = (await core.request("GET", "/proxies"))["proxies"]
                proxy = live.get(failed_name or name, {})
                history = proxy.get("extra", {}).get(store.state["settings"]["test_url"], {}).get("history", [])
                probe_ok = history and (history[-1]["delay"] == 0 if failed_name else history[-1]["delay"] > 0)
                if live["AUTO"]["now"] == name and probe_ok:
                    return
                await asyncio.sleep(.2)
            raise AssertionError("自动组未在检测后选择预期节点：" + json.dumps({
                "auto":live["AUTO"],
                "nodes":{node["name"]:live[node["core_name"]] for node in (fast,backup)},
            }, ensure_ascii=False))

        try:
            await manager.start()
            assert core.running, manager.error
            await wait_for_selected(fast["core_name"])
            assert (await core.request("GET", "/proxies/OUTBOUND"))["now"] == "AUTO"
            servers[0].healthy = False
            await wait_for_selected(backup["core_name"], fast["core_name"])
            async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{store.state['settings']['proxy_port']}",
                                         trust_env=False) as client:
                assert (await client.get("http://probe.invalid/after-failover", timeout=5)).status_code == 204
            await manager.select(fast["id"])
            assert (await core.request("GET", "/proxies/OUTBOUND"))["now"] == fast["core_name"]
            assert store.state["selected"] == fast["id"]
        finally:
            await manager.close()

    try:
        asyncio.run(scenario())
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
