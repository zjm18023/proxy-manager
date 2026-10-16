import asyncio
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
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not (ROOT/"bin"/"mihomo.exe").exists(),reason="需要已安装的 Windows Mihomo")
def test_real_primary_preference_failure_recovery_manual_override_and_reject(tmp_path,monkeypatch):
    class Proxy(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self,*args):
            pass

        def do_CONNECT(self):
            if not self.server.healthy:
                self.send_response(503)
                self.send_header("Content-Length","0")
                self.close_connection = True
            else:
                self.send_response(200)
            self.end_headers()

        def do_HEAD(self):
            if not self.server.healthy:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            time.sleep(self.server.delay)
            self.send_response(204)
            self.end_headers()

        do_GET = do_HEAD

    servers = []
    for delay in (.16,.025):
        server = ThreadingHTTPServer(("127.0.0.1",0),Proxy)
        server.healthy,server.delay = True,delay
        threading.Thread(target=server.serve_forever,daemon=True).start()
        servers.append(server)
    monkeypatch.setenv("PROXY_MANAGER_CORE_PORT",str(free_port()))

    async def scenario():
        store = Store(tmp_path)
        # Faster probes only for this isolated test; real user settings are untouched.
        store.state["settings"].update(proxy_port=free_port(),test_interval=2,
            test_url="http://probe.invalid/check",selection_strategy="primary",primary_subscription_id="main")
        store.state["subscriptions"] = [
            {"id":identifier,"name":name,"url":"https://example.org/unused","enabled":True,
             "last_attempt":time.time(),"last_success":None,"error":"","warnings":[],
             "nodes":[{"name":name,"type":"http","server":"127.0.0.1","port":server.server_port}]}
            for identifier,name,server in zip(("main","backup"),("日本主线路","新加坡备用"),servers)
        ]
        core = Core(tmp_path,store.state["controller_secret"],ROOT)
        manager = Manager(store,core)
        main,backup = inventory(store.state)

        async def wait_for(predicate):
            deadline = time.monotonic()+18
            while time.monotonic()<deadline:
                status = await manager.status()
                if predicate(status):
                    return status
                await asyncio.sleep(.2)
            raise AssertionError("主备状态未达到预期："+str(status["routing"]))

        async def proxy_request():
            async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{store.state['settings']['proxy_port']}",trust_env=False) as client:
                return await client.get("http://probe.invalid/request",timeout=5)

        try:
            await manager.start()
            assert core.running,manager.error
            status = await wait_for(lambda s:s["routing"]["active_tier"]=="primary")
            assert status["current_node"] == main["id"]
            assert (await proxy_request()).status_code == 204
            # A fast backup never outranks an available primary.
            assert status["nodes"][0]["delay"] > status["nodes"][1]["delay"]
            servers[0].healthy = False
            status = await wait_for(lambda s:s["routing"]["active_tier"]=="backup")
            assert status["current_node"] == backup["id"]
            assert (await proxy_request()).status_code == 204
            servers[0].healthy = True
            recovering = await wait_for(lambda s:s["routing"].get("reason")=="primary_recovering")
            assert recovering["routing"]["active_tier"] == "backup"
            assert recovering["routing"]["recovery_successes"] == 1
            await wait_for(lambda s:s["routing"]["active_tier"]=="primary")
            # Manual choice takes precedence while the primary policy keeps running in the background.
            await manager.select(backup["id"])
            await asyncio.sleep(2.2)
            manual = await manager.status()
            assert manual["current_node"] == backup["id"] and manual["routing"]["active_tier"]=="manual"
            await manager.select("AUTO")
            await wait_for(lambda s:s["routing"]["active_tier"]=="primary")
            servers[0].healthy = servers[1].healthy = False
            await wait_for(lambda s:s["routing"].get("branch")=="REJECT")
            try:
                assert (await proxy_request()).status_code != 204
            except httpx.HTTPError:
                pass
            # The same source preference must still honor region exclusions in real core groups.
            servers[1].healthy = True
            await manager.save_settings(store.state["settings"] | {"excluded_regions":["JP"]})
            filtered = await wait_for(lambda s:s["routing"]["active_tier"]=="backup")
            assert filtered["routing"]["primary_candidates"] == 0
            assert filtered["routing"]["reason"] == "primary_filtered"
            assert (await proxy_request()).status_code == 204
            # Removing the designated primary restores the original global strategy.
            await manager.delete_subscription("main")
            assert store.state["settings"]["selection_strategy"] == "latency"
            assert (await core.request("GET","/proxies/AUTO"))["type"] == "URLTest"
        finally:
            await manager.close()

    try:
        asyncio.run(scenario())
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
