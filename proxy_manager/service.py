from __future__ import annotations

import asyncio
import contextlib
import copy
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import httpx

from .core import Core, inventory
from .store import Store
from .subscriptions import REGIONS, fetch_subscription
from .routing import PrimaryPreference, resolve_node


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Manager:
    def __init__(self, store: Store, core: Core):
        self.store = store
        self.core = core
        self.lock = asyncio.Lock()
        self.events: deque = deque(maxlen=200)
        self.health: dict[str, dict] = {}
        self.error = ""
        self.applied = False
        self.test_task: asyncio.Task | None = None
        self.job = {"running": False, "done": 0, "total": 0, "error": ""}
        self.scheduler: asyncio.Task | None = None
        self.syncing = False
        self.applying = False
        self.routing_task: asyncio.Task | None = None
        self.preference = PrimaryPreference()
        self.routing_state: dict = {}

    def event(self, message: str, level: str = "info"):
        self.events.appendleft({"time": now(), "message": message, "level": level})

    async def start(self):
        try:
            await self.core.apply(self.store.state)
            self.applied = True
            self.event("代理服务已启动")
        except (ValueError, OSError) as exc:
            self.error = str(exc) if isinstance(exc, ValueError) else "代理内核无法运行，请检查安装位置"
            self.event(self.error, "error")
        self.scheduler = asyncio.create_task(self._schedule())
        self.routing_task = asyncio.create_task(self._route_loop())

    async def close(self):
        for task in [self.scheduler, self.routing_task, self.test_task]:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.core.close()

    async def cancel_tests(self):
        if self.test_task and not self.test_task.done():
            self.test_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.test_task
        self.job["running"] = False

    async def commit(self, state: dict):
        # All calls hold lock: mutations become durable only after core validation and reload succeed.
        eligible = {n["id"] for n in inventory(state) if n["eligible"]}
        if state["selected"] != "AUTO" and state["selected"] not in eligible:
            state["selected"] = "AUTO"
        await self.cancel_tests()
        self.applying = True
        previous = self.store.snapshot()
        try:
            await self.core.apply(state)
            self.store.state = state
            try:
                self.store.save()
            except OSError:
                self.store.state = previous
                await self.core.apply(previous)
                raise ValueError("配置保存失败，请检查数据目录是否可写") from None
            self.applied = True
            self.error = ""
            old_settings, new_settings = previous["settings"], state["settings"]
            if (old_settings.get("selection_strategy"), old_settings.get("primary_subscription_id")) != (
                    new_settings.get("selection_strategy"), new_settings.get("primary_subscription_id")):
                self.preference, self.routing_state = PrimaryPreference(), {}
            if old_settings["test_url"] != new_settings["test_url"]:
                self.health.clear()
            if self.core.running:
                try:
                    await self._reconcile_primary()
                except httpx.HTTPError:
                    self.routing_state["error"] = "线路状态暂时无法确认，正在重新检查"
        finally:
            self.applying = False

    async def save_subscription(self, payload: dict, identifier: str | None = None):
        async with self.lock:
            state = self.store.snapshot()
            sub = next((s for s in state["subscriptions"] if s["id"] == identifier), None)
            if identifier and not sub:
                raise ValueError("订阅不存在")
            if sub is None:
                sub = {"id": uuid.uuid4().hex, "nodes": [], "last_success": None,
                       "last_attempt": 0, "error": "", "warnings": []}
                state["subscriptions"].append(sub)
            if sub.get("url") and sub["url"] != payload["url"]:
                sub.update(nodes=[], last_success=None, last_attempt=0, error="", warnings=[])
            sub.update(payload)
            # Adding an empty subscription works even before the core has been installed.
            if self.core.binary:
                await self.commit(state)
            else:
                self.store.state = state
                self.store.save()
            self.event(f"订阅“{sub['name']}”已保存")

    async def delete_subscription(self, identifier: str):
        async with self.lock:
            state = self.store.snapshot()
            sub = next((s for s in state["subscriptions"] if s["id"] == identifier), None)
            if not sub:
                raise ValueError("订阅不存在")
            state["subscriptions"].remove(sub)
            was_primary = state["settings"].get("primary_subscription_id") == identifier
            if was_primary:
                state["settings"].update(selection_strategy="latency", primary_subscription_id=None)
            if self.core.binary:
                await self.commit(state)
            else:
                self.store.state = state
                self.store.save()
            self.event(f"订阅“{sub['name']}”已删除")
            if was_primary:
                self.event("主订阅已删除，已恢复全部订阅择优")

    async def refresh(self, identifiers: list[str] | None = None):
        if self.syncing:
            raise ValueError("订阅正在更新，请稍候")
        self.syncing = True
        results = []
        try:
            async with self.lock:
                sources = [copy.deepcopy(s) for s in self.store.state["subscriptions"]
                           if s["enabled"] and (identifiers is None or s["id"] in identifiers)]
            if not sources:
                raise ValueError("没有可更新的订阅，请先添加并启用订阅")
            semaphore = asyncio.Semaphore(3)

            async def fetch(sub):
                async with semaphore:
                    try:
                        nodes, warnings = await fetch_subscription(sub["url"])
                        return sub, nodes, warnings, ""
                    except (ValueError, RecursionError) as exc:
                        return sub, None, [], str(exc) if isinstance(exc, ValueError) else "订阅结构过于复杂"

            downloaded = await asyncio.gather(*(fetch(s) for s in sources))
            async with self.lock:
                for source, nodes, warnings, error in downloaded:
                    state = self.store.snapshot()
                    target = next((s for s in state["subscriptions"] if s["id"] == source["id"]), None)
                    if not target or target["url"] != source["url"] or not target["enabled"]:
                        continue
                    if nodes is not None:
                        target.update(nodes=nodes, warnings=warnings, error="", last_success=now(), last_attempt=time.time())
                        try:
                            await self.commit(state)
                            self.event(f"订阅“{target['name']}”更新成功，共 {len(nodes)} 个节点")
                            results.append({"id": target["id"], "ok": True, "count": len(nodes)})
                            continue
                        except (ValueError, OSError) as exc:
                            error = str(exc) if isinstance(exc, ValueError) else "节点应用失败，请检查数据目录和内核"
                    # Failed fetch or validation must never destroy the last working subscription cache.
                    current = next(s for s in self.store.state["subscriptions"] if s["id"] == source["id"])
                    current.update(error=error, last_attempt=time.time())
                    self.store.save()
                    self.event(f"订阅“{current['name']}”更新失败，保留上次节点：{error}", "error")
                    results.append({"id": current["id"], "ok": False, "error": error})
            return results
        finally:
            self.syncing = False

    async def save_settings(self, settings: dict):
        async with self.lock:
            state = self.store.snapshot()
            self._validate_routing(settings, state)
            state["settings"] = settings
            await self.commit(state)
            self.event("筛选与代理设置已应用")

    @staticmethod
    def _validate_routing(settings: dict, state: dict):
        if settings.get("selection_strategy") == "primary":
            primary = next((s for s in state["subscriptions"] if s["id"] == settings.get("primary_subscription_id")), None)
            if not primary:
                raise ValueError("主订阅不存在，请重新选择")
        else:
            settings["primary_subscription_id"] = None

    async def save_routing(self, strategy: str, primary_id: str | None):
        async with self.lock:
            state = self.store.snapshot()
            if strategy == "primary":
                primary = next((s for s in state["subscriptions"] if s["id"] == primary_id), None)
                if not primary or not primary["enabled"]:
                    raise ValueError("请选择一个已启用的主订阅")
            else:
                primary_id = None
            state["settings"].update(selection_strategy=strategy, primary_subscription_id=primary_id)
            await self.commit(state)
            self.event(f"已将“{primary['name']}”设为主订阅" if strategy == "primary" else "已恢复全部订阅择优")

    async def _reconcile_primary(self):
        settings = self.store.state["settings"]
        if settings.get("selection_strategy") != "primary" or not self.core.running:
            return
        live = (await self.core.request("GET", "/proxies", timeout=3)).get("proxies", {})
        decision = self.preference.choose(settings, self.store.state["subscriptions"], inventory(self.store.state), live)
        branch = decision["branch"]
        previous = live.get("AUTO", {}).get("now")
        if previous != branch:
            await self.core.request("PUT", "/proxies/AUTO", json={"name":branch})
            if self.store.state["selected"] == "AUTO":
                self.event({"PRIMARY":"自动出口已使用主订阅", "BACKUP":"主订阅暂无可用节点，自动出口已使用备用订阅",
                            "REJECT":"主备订阅均无可用节点，自动出口已暂停连接"}[branch])
        self.routing_state = decision

    async def _route_loop(self):
        while True:
            await asyncio.sleep(2)
            if self.store.state["settings"].get("selection_strategy") != "primary":
                continue
            try:
                async with self.lock:
                    await self._reconcile_primary()
            except httpx.HTTPError:
                self.routing_state["error"] = "线路状态暂时无法确认，正在重新检查"

    async def override(self, identifier: str, payload: dict):
        async with self.lock:
            state = self.store.snapshot()
            if identifier not in {n["id"] for n in inventory(state)}:
                raise ValueError("节点不存在，请刷新列表")
            state["overrides"].setdefault(identifier, {}).update(payload)
            await self.commit(state)
            self.event("节点设置已应用")

    async def select(self, identifier: str):
        async with self.lock:
            if not self.core.running or not self.applied:
                raise ValueError("代理服务尚未就绪，请先启动内核")
            state = self.store.snapshot()
            node = next((n for n in inventory(state) if n["id"] == identifier and n["eligible"]), None)
            if identifier != "AUTO" and not node:
                raise ValueError("节点不存在或已被排除")
            if identifier == "AUTO":
                try:
                    await self._reconcile_primary()
                except httpx.HTTPError:
                    raise ValueError("暂时无法确认主备线路，请稍后重试") from None
            state["selected"] = identifier
            try:
                await self.core.request("PUT", "/proxies/OUTBOUND", json={"name": node["core_name"] if node else "AUTO"})
                previous = self.store.state
                self.store.state = state
                try:
                    self.store.save()
                except OSError:
                    self.store.state = previous
                    await self.core.apply(previous)
                    raise ValueError("选择保存失败，已恢复之前的出口") from None
            except httpx.HTTPError:
                raise ValueError("节点切换失败，请检查代理服务状态") from None
            self.event("已启用自动选择" if not node else f"已选择节点“{node['name']}”")

    async def start_tests(self, identifier: str | None):
        async with self.lock:
            if not self.core.running or not self.applied:
                raise ValueError("代理服务尚未就绪，请先启动内核")
            if self.job["running"]:
                raise ValueError("正在检测节点，请等待本次检测完成")
            nodes = [n for n in inventory(self.store.state) if n["eligible"] and (not identifier or n["id"] == identifier)]
            if not nodes:
                raise ValueError("没有可检测的节点")
            self.job = {"running": True, "done": 0, "total": len(nodes), "error": ""}
            self.test_task = asyncio.create_task(self._test(nodes, self.store.state["settings"]["test_url"]))

    async def _test(self, nodes, url):
        semaphore = asyncio.Semaphore(8)

        async def one(node):
            async with semaphore:
                delay = await self.core.delay(node["core_name"], url)
                self.health[node["id"]] = {"delay": delay, "checked_at": now()}
                self.job["done"] += 1
        try:
            await asyncio.gather(*(one(n) for n in nodes))
            self.event(f"节点检测完成，共检测 {len(nodes)} 个节点")
        except Exception:
            self.job["error"] = "检测中断，请检查代理服务状态后重试"
        finally:
            self.job["running"] = False

    async def status(self):
        nodes = inventory(self.store.state)
        live = {}
        connected = False
        if self.core.running and not self.applying:
            try:
                live = (await self.core.request("GET", "/proxies", timeout=2)).get("proxies", {})
                connected = True
            except httpx.HTTPError:
                pass
        for node in nodes:
            proxy = live.get(node["core_name"], {})
            probe = proxy.get("extra", {}).get(self.store.state["settings"]["test_url"])
            # Aggregate latency can be positive even when the expected HTTP status failed.
            # Per-URL history reflects this probe's actual health and records failed probes as zero.
            history = probe.get("history", []) if isinstance(probe, dict) else proxy.get("history", [])
            check = self.health.get(node["id"])
            # Prefer the newest result, including automatic tests performed by mihomo.
            if history:
                last = history[-1]
                timestamp = last.get("time", "")
                try:
                    more_recent = not check or datetime.fromisoformat(timestamp.replace("Z", "+00:00")) > datetime.fromisoformat(check["checked_at"])
                except ValueError:
                    more_recent = not check
                if more_recent:
                    check = {"delay": last.get("delay") or None, "checked_at": timestamp}
            node.update(check or {"delay": None, "checked_at": None})
            node["status"] = "excluded" if not node["eligible"] else (
                "unknown" if not node["checked_at"] else "available" if node["delay"] else "unavailable")
            del node["raw"]
        current_name = resolve_node(live)
        current = next((n for n in nodes if n["core_name"] == current_name), None)
        settings = self.store.state["settings"]
        primary_id = settings.get("primary_subscription_id")
        primary = next((s for s in self.store.state["subscriptions"] if s["id"] == primary_id), None)
        tier = "none"
        if current and connected:
            tier = "manual" if self.store.state["selected"] != "AUTO" else (
                "latency" if settings.get("selection_strategy", "latency") != "primary" else
                "primary" if current["subscription_id"] == primary_id else "backup")
        routing = {**self.routing_state, "strategy":settings.get("selection_strategy", "latency"),
                   "primary_subscription_id":primary_id, "primary_name":primary["name"] if primary else None,
                   "active_tier":tier, "confirmed":connected,
                   "active_subscription_id":current["subscription_id"] if current and connected else None,
                   "required_successes":PrimaryPreference.required_successes}
        safe_subscriptions = [{k: v for k, v in s.items() if k not in {"nodes", "last_attempt"}} |
                              {"node_count": len(s["nodes"])} for s in self.store.state["subscriptions"]]
        return {
            "settings": self.store.state["settings"], "subscriptions": safe_subscriptions, "nodes": nodes,
            "regions": [{"code": code, "name": name} for code, (name, _) in REGIONS.items()],
            "selected": self.store.state["selected"], "current_node": current["id"] if current else None,
            "core": {"installed": bool(self.core.binary), "running": connected, "error": self.error,
                     "port": self.store.state["settings"]["proxy_port"]},
            "job": self.job.copy(), "syncing": self.syncing, "events": list(self.events),
            "routing": routing,
        }

    async def _schedule(self):
        retry_at = time.monotonic() + 60
        while True:
            await asyncio.sleep(10)
            try:
                if not self.core.running and self.core.binary and time.monotonic() >= retry_at:
                    retry_at = time.monotonic() + 60
                    async with self.lock:
                        await self.core.apply(self.store.state)
                        self.applied, self.error = True, ""
                        self.event("代理服务已恢复运行")
                interval = self.store.state["settings"]["refresh_hours"] * 3600
                due = [s["id"] for s in self.store.state["subscriptions"] if s["enabled"]
                       and time.time() - s["last_attempt"] >= interval]
                if due and not self.syncing:
                    await self.refresh(due)
            except (ValueError, OSError) as exc:
                self.error = str(exc) if isinstance(exc, ValueError) else "服务恢复失败，请检查内核与数据目录"
                self.event(self.error, "error")
