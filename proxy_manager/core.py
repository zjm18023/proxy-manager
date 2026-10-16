from __future__ import annotations

import asyncio
import copy
import os
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

from .store import atomic_write
from .subscriptions import REGIONS, detect_region, node_id


def inventory(state: dict) -> list[dict]:
    settings = state["settings"]
    keywords = [v.strip().casefold() for v in settings["exclude_keywords"].splitlines() if v.strip()]
    nodes = []
    for sub in state["subscriptions"]:
        for raw in sub["nodes"]:
            identifier = node_id(sub["id"], raw)
            override = state["overrides"].get(identifier, {})
            region = override.get("region") or detect_region(raw["name"])
            reason = ""
            if not sub["enabled"]:
                reason = "订阅已停用"
            elif override.get("disabled", False):
                reason = "已手动停用"
            elif region in settings["excluded_regions"]:
                reason = "地区已排除"
            elif any(v in raw["name"].casefold() for v in keywords):
                reason = "关键词已排除"
            nodes.append({
                "id": identifier, "name": raw["name"], "subscription_id": sub["id"],
                "subscription": sub["name"], "region": region, "region_name": REGIONS[region][0],
                "type": raw["type"], "eligible": not reason, "excluded_reason": reason,
                "manual_disabled": override.get("disabled", False),
                "core_name": f"{sub['name']} · {raw['name']} [{identifier}]", "raw": raw,
            })
    return nodes


def build_config(state: dict, controller_port: int) -> str:
    settings = state["settings"]
    candidates = [n for n in inventory(state) if n["eligible"]]
    proxies = []
    maps: dict[str, dict[str, str]] = {}
    for node in candidates:
        maps.setdefault(node["subscription_id"], {})[node["name"]] = node["core_name"]
    for node in candidates:
        raw = copy.deepcopy(node["raw"])
        raw["name"] = node["core_name"]
        if raw.get("dialer-proxy"):
            target = maps[node["subscription_id"]].get(raw["dialer-proxy"])
            if not target:
                raise ValueError(f"节点“{node['name']}”依赖的前置节点不存在或已排除")
            raw["dialer-proxy"] = target
        proxies.append(raw)
    names = [n["core_name"] for n in candidates]
    def probe_group(name, members):
        return {"name":name, "type":"url-test", "proxies":members or ["REJECT"],
                "url":settings["test_url"], "interval":settings["test_interval"],
                "tolerance":50, "lazy":False, "timeout":5000, "expected-status":"200/204"}

    groups = [probe_group("AUTO", names)]
    if settings.get("selection_strategy") == "primary":
        primary_id = settings.get("primary_subscription_id")
        groups = [
            probe_group("PRIMARY", [n["core_name"] for n in candidates if n["subscription_id"] == primary_id]),
            probe_group("BACKUP", [n["core_name"] for n in candidates if n["subscription_id"] != primary_id]),
            # The manager selects a healthy tier; default denies until probes are known.
            {"name":"AUTO", "type":"select", "proxies":["REJECT", "PRIMARY", "BACKUP"]},
        ]
    selected = next((n["core_name"] for n in candidates if n["id"] == state["selected"]), "AUTO")
    outbound_names = [selected] + [v for v in ["AUTO", *names] if v != selected]
    result = {
        "mixed-port": settings["proxy_port"], "allow-lan": settings["allow_lan"],
        "bind-address": "*" if settings["allow_lan"] else "127.0.0.1",
        "mode": "rule", "log-level": "warning", "ipv6": False,
        "external-controller": f"127.0.0.1:{controller_port}", "secret": state["controller_secret"],
        "profile": {"store-selected": False}, "proxies": proxies,
        "proxy-groups": groups + [{"name":"OUTBOUND", "type":"select", "proxies":outbound_names}],
        "rules": ["MATCH,OUTBOUND"],
    }
    if settings["allow_lan"]:
        result["lan-allowed-ips"] = settings["allowed_ips"]
    if settings["proxy_username"]:
        result["authentication"] = [f"{settings['proxy_username']}:{settings['proxy_password']}"]
    return yaml.safe_dump(result, allow_unicode=True, sort_keys=False)


class Core:
    def __init__(self, directory: Path, secret: str, root: Path):
        self.directory = directory / "core"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.secret = secret
        self.port = int(os.environ.get("PROXY_MANAGER_CORE_PORT", "9091"))
        default_bin = root / "bin" / ("mihomo.exe" if os.name == "nt" else "mihomo")
        self.binary = os.environ.get("MIHOMO_BIN") or (str(default_bin) if default_bin.exists() else shutil.which("mihomo"))
        self.process: asyncio.subprocess.Process | None = None
        self.log = None
        self.proxy_port: int | None = None
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{self.port}",
                                       headers={"Authorization": f"Bearer {secret}"},
                                       trust_env=False, timeout=12)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def request(self, method: str, path: str, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    async def validate(self, payload: str) -> None:
        if not self.binary:
            raise ValueError("尚未安装代理内核，请先运行安装内核脚本")
        candidate = self.directory / "candidate.yaml"
        atomic_write(candidate, payload)
        proc = await asyncio.create_subprocess_exec(
            self.binary, "-t", "-d", str(self.directory), "-f", str(candidate),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ValueError("代理配置验证超时，请检查节点配置") from None
        if proc.returncode != 0:
            atomic_write(self.directory / "validation.log", output.decode("utf-8", errors="replace"))
            raise ValueError("代理内核未通过配置检查。请检查订阅格式；详细原因已保存在 data\\core\\validation.log")

    @staticmethod
    def check_port(port: int, host: str = "127.0.0.1") -> None:
        with socket.socket() as probe:
            try:
                probe.bind((host, port))
            except OSError:
                raise ValueError(f"端口 {port} 已被占用，请关闭占用程序或更换端口") from None

    async def apply(self, state: dict) -> None:
        payload = build_config(state, self.port)
        await self.validate(payload)
        new_port = state["settings"]["proxy_port"]
        if new_port != self.proxy_port or not self.running:
            self.check_port(new_port, "0.0.0.0" if state["settings"]["allow_lan"] else "127.0.0.1")
        if self.running:
            previous_payload = (self.directory / "config.yaml").read_text(encoding="utf-8")
            previous_selected = None
            try:
                previous_selected = (await self.request("GET", "/proxies/OUTBOUND")).get("now")
                await self.request("PUT", "/configs", params={"force": "true"}, json={"payload": payload})
                target = next((n["core_name"] for n in inventory(state)
                               if n["eligible"] and n["id"] == state["selected"]), "AUTO")
                await self.request("PUT", "/proxies/OUTBOUND", json={"name": target})
            except httpx.HTTPError:
                try:
                    await self.request("PUT", "/configs", params={"force": "true"}, json={"payload": previous_payload})
                    if previous_selected:
                        await self.request("PUT", "/proxies/OUTBOUND", json={"name": previous_selected})
                except httpx.HTTPError:
                    await self.stop()
                raise ValueError("应用代理配置失败，已撤销本次修改，请检查内核运行状态") from None
        else:
            self.check_port(self.port)
            atomic_write(self.directory / "config.yaml", payload)
            if self.log:
                self.log.close()
            self.log = (self.directory / "runtime.log").open("ab")
            self.process = await asyncio.create_subprocess_exec(
                self.binary, "-d", str(self.directory), "-f", str(self.directory / "config.yaml"),
                stdout=self.log, stderr=self.log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            ready = False
            for _ in range(50):
                if self.process.returncode is not None:
                    break
                try:
                    await self.request("GET", "/version", timeout=0.4)
                    ready = True
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.15)
            if not ready:
                await self.stop()
                raise ValueError("代理内核启动失败，请检查端口及 data\\core\\runtime.log")
        atomic_write(self.directory / "config.yaml", payload)
        self.proxy_port = new_port

    async def stop(self):
        if self.running:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.log:
            self.log.close()
            self.log = None

    async def close(self):
        await self.stop()
        await self.client.aclose()

    async def delay(self, name: str, url: str) -> int | None:
        try:
            result = await self.request("GET", f"/proxies/{quote(name, safe='')}/delay",
                                        params={"url": url, "timeout": 5000, "expected": "200/204"}, timeout=7)
            return result.get("delay") or None
        except httpx.HTTPError:
            return None
