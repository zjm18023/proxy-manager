import asyncio
import copy

import pytest
import yaml

from proxy_manager.core import build_config, inventory
from proxy_manager.service import Manager
from proxy_manager.store import Store


class StubCore:
    binary = "stub"
    running = False
    port = 9091
    fail = False

    async def apply(self, state):
        build_config(state, self.port)
        if self.fail:
            raise ValueError("测试配置无效")


def source(identifier="one", name="订阅一", region="日本"):
    return {"id":identifier, "name":name, "url":"https://example.org/feed", "enabled":True,
            "last_attempt":0, "last_success":None, "error":"", "warnings":[],
            "nodes":[{"name":region, "type":"http", "server":"localhost", "port":8080}]}


def test_exclusions_apply_to_all_subscriptions_and_never_direct(tmp_path):
    store = Store(tmp_path)
    store.state["subscriptions"] = [source(), source("two", "订阅二")]
    names = [n["core_name"] for n in inventory(store.state)]
    assert len(set(names)) == 2
    store.state["settings"]["excluded_regions"] = ["JP"]
    assert not any(n["eligible"] for n in inventory(store.state))
    config = yaml.safe_load(build_config(store.state, 9091))
    assert config["proxies"] == []
    assert config["proxy-groups"][0]["proxies"] == ["REJECT"]
    assert config["rules"] == ["MATCH,OUTBOUND"]
    assert "DIRECT" not in str(config)


def test_manual_region_and_disabled_override_are_persistent(tmp_path):
    store = Store(tmp_path)
    store.state["subscriptions"] = [source()]
    identifier = inventory(store.state)[0]["id"]
    store.state["overrides"][identifier] = {"region":"US", "disabled":True}
    store.save()
    loaded = inventory(Store(tmp_path).state)[0]
    assert loaded["region"] == "US"
    assert loaded["excluded_reason"] == "已手动停用"
    assert not loaded["eligible"]


def test_failed_reload_does_not_commit_settings(tmp_path):
    async def scenario():
        store = Store(tmp_path)
        core = StubCore()
        manager = Manager(store, core)
        original = store.snapshot()
        settings = copy.deepcopy(original["settings"])
        settings["excluded_regions"] = ["HK"]
        core.fail = True
        with pytest.raises(ValueError):
            await manager.save_settings(settings)
        assert store.state == original
        assert Store(tmp_path).state == original
    asyncio.run(scenario())


def test_failed_refresh_keeps_last_working_nodes(tmp_path, monkeypatch):
    async def failed(url):
        raise ValueError("订阅服务器不可用")
    monkeypatch.setattr("proxy_manager.service.fetch_subscription", failed)

    async def scenario():
        store = Store(tmp_path)
        store.state["subscriptions"] = [source()]
        store.save()
        original = copy.deepcopy(store.state["subscriptions"][0]["nodes"])
        manager = Manager(store, StubCore())
        results = await manager.refresh()
        assert results[0]["ok"] is False
        assert store.state["subscriptions"][0]["nodes"] == original
        assert Store(tmp_path).state["subscriptions"][0]["nodes"] == original
        assert store.state["subscriptions"][0]["error"]
    asyncio.run(scenario())


def test_invalid_new_nodes_keep_cache_while_other_subscription_updates(tmp_path, monkeypatch):
    async def fetched(url):
        name = "bad" if "bad" in url else "美国"
        return [{"name":name, "type":"http", "server":"localhost", "port":8081}], []
    monkeypatch.setattr("proxy_manager.service.fetch_subscription", fetched)

    class ValidatingCore(StubCore):
        async def apply(self, state):
            if any(n["name"] == "bad" for s in state["subscriptions"] for n in s["nodes"]):
                raise ValueError("无效节点")

    async def scenario():
        store = Store(tmp_path)
        store.state["subscriptions"] = [source(), source("two", "订阅二")]
        store.state["subscriptions"][0]["url"] += "/bad"
        manager = Manager(store, ValidatingCore())
        results = await manager.refresh()
        assert [r["ok"] for r in results] == [False, True]
        assert store.state["subscriptions"][0]["nodes"][0]["name"] == "日本"
        assert store.state["subscriptions"][1]["nodes"][0]["name"] == "美国"
    asyncio.run(scenario())


def test_excluded_manual_selection_returns_to_auto(tmp_path):
    async def scenario():
        store = Store(tmp_path)
        store.state["subscriptions"] = [source()]
        identifier = inventory(store.state)[0]["id"]
        store.state["selected"] = identifier
        manager = Manager(store, StubCore())
        await manager.override(identifier, {"disabled":True})
        assert store.state["selected"] == "AUTO"
    asyncio.run(scenario())


def test_failed_http_probe_is_not_available_even_with_aggregate_latency(tmp_path):
    async def scenario():
        store = Store(tmp_path)
        store.state["subscriptions"] = [source()]
        node = inventory(store.state)[0]
        timestamp = "2026-10-15T10:00:00+08:00"
        url = store.state["settings"]["test_url"]

        class ReportingCore(StubCore):
            running = True

            async def request(self, method, path, **kwargs):
                return {"proxies":{
                    node["core_name"]:{
                        "alive":True, "history":[{"time":timestamp,"delay":30}],
                        "extra":{url:{"alive":False,"history":[{"time":timestamp,"delay":0}]}},
                    },
                    "OUTBOUND":{"now":"AUTO"}, "AUTO":{"now":node["core_name"]},
                }}

        result = await Manager(store, ReportingCore()).status()
        assert result["nodes"][0]["status"] == "unavailable"
        assert result["nodes"][0]["delay"] is None
    asyncio.run(scenario())
