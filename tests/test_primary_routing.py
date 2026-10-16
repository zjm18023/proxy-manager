import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from proxy_manager.core import build_config, inventory
from proxy_manager.routing import PrimaryPreference, resolve_node
from proxy_manager.service import Manager
from proxy_manager.store import Store


def subscription(identifier, name):
    return {"id":identifier,"name":name,"url":"https://example.org/feed","enabled":True,
            "last_attempt":0,"last_success":None,"error":"","warnings":[],
            "nodes":[{"name":name,"type":"http","server":"localhost","port":8080}]}


def setup_state(tmp_path):
    store = Store(tmp_path)
    store.state["subscriptions"] = [subscription("main","日本主线路"),subscription("backup","新加坡备用")]
    store.state["settings"].update(selection_strategy="primary",primary_subscription_id="main")
    return store


def live_results(state, primary=True, backup=True, offset=0, branch="PRIMARY"):
    timestamp = (datetime(2026,10,15,tzinfo=timezone.utc)+timedelta(seconds=offset)).isoformat()
    url = state["settings"]["test_url"]
    live = {"AUTO":{"now":branch}}
    for node in inventory(state):
        healthy = primary if node["subscription_id"] == "main" else backup
        live[node["core_name"]] = {"extra":{url:{"alive":healthy,"history":[{
            "time":timestamp,"delay":180 if node["subscription_id"] == "main" and healthy else 20 if healthy else 0}]}}}
    return live


def test_primary_is_preferred_even_if_backup_is_faster(tmp_path):
    store = setup_state(tmp_path)
    router = PrimaryPreference()
    result = router.choose(store.state["settings"],store.state["subscriptions"],inventory(store.state),live_results(store.state))
    assert result["branch"] == "PRIMARY"


def test_failover_waits_for_two_distinct_successful_rounds_to_return(tmp_path):
    store = setup_state(tmp_path)
    router = PrimaryPreference()
    def choose(**kwargs):
        return router.choose(store.state["settings"],store.state["subscriptions"],inventory(store.state),live_results(store.state,**kwargs))
    assert choose(primary=False)["branch"] == "BACKUP"
    recovering = choose(offset=300,branch="BACKUP")
    assert recovering["branch"] == "BACKUP" and recovering["recovery_successes"] == 1
    for _ in range(5):
        assert choose(offset=300,branch="BACKUP")["recovery_successes"] == 1
    assert choose(offset=310,branch="BACKUP")["branch"] == "BACKUP"
    assert choose(offset=600,branch="BACKUP")["branch"] == "PRIMARY"


def test_interrupted_recovery_restarts_stability_check(tmp_path):
    store = setup_state(tmp_path)
    router = PrimaryPreference()
    def choose(**kwargs):
        return router.choose(store.state["settings"],store.state["subscriptions"],inventory(store.state),live_results(store.state,**kwargs))
    choose(primary=False)
    choose(offset=300,branch="BACKUP")
    assert choose(primary=False,offset=600,branch="BACKUP")["recovery_successes"] == 0
    assert choose(offset=900,branch="BACKUP")["branch"] == "BACKUP"
    assert choose(offset=1200,branch="BACKUP")["branch"] == "PRIMARY"


def test_restore_available_primary_immediately_when_backup_also_fails(tmp_path):
    store = setup_state(tmp_path)
    router = PrimaryPreference()
    router.choose(store.state["settings"],store.state["subscriptions"],inventory(store.state),live_results(store.state,primary=False))
    decision = router.choose(store.state["settings"],store.state["subscriptions"],inventory(store.state),
                             live_results(store.state,backup=False,branch="BACKUP",offset=300))
    assert decision["branch"] == "PRIMARY"


def test_both_unavailable_rejects_without_direct(tmp_path):
    store = setup_state(tmp_path)
    result = PrimaryPreference().choose(store.state["settings"],store.state["subscriptions"],inventory(store.state),
                                         live_results(store.state,primary=False,backup=False))
    assert result["branch"] == "REJECT"


@pytest.mark.parametrize("disabled,excluded,reason", [(True,False,"primary_disabled"),(False,True,"primary_filtered")])
def test_disabled_and_filtered_primary_use_backup(tmp_path, disabled, excluded, reason):
    store = setup_state(tmp_path)
    if disabled:
        store.state["subscriptions"][0]["enabled"] = False
    if excluded:
        store.state["settings"]["excluded_regions"] = ["JP"]
    result = PrimaryPreference().choose(store.state["settings"],store.state["subscriptions"],inventory(store.state),live_results(store.state))
    assert result["branch"] == "BACKUP" and result["reason"] == reason


def test_groups_keep_source_preference_after_filters(tmp_path):
    store = setup_state(tmp_path)
    store.state["settings"]["excluded_regions"] = ["JP"]
    config = yaml.safe_load(build_config(store.state,9091))
    groups = {g["name"]:g for g in config["proxy-groups"]}
    assert groups["PRIMARY"]["proxies"] == ["REJECT"]
    assert groups["BACKUP"]["proxies"] == [inventory(store.state)[1]["core_name"]]
    assert groups["AUTO"]["type"] == "select"
    assert "DIRECT" not in str(config)


def test_nested_group_resolution_and_cycle_protection():
    assert resolve_node({"OUTBOUND":{"now":"AUTO"},"AUTO":{"now":"PRIMARY"},"PRIMARY":{"now":"node"}}) == "node"
    assert resolve_node({"OUTBOUND":{"now":"AUTO"},"AUTO":{"now":"OUTBOUND"}}) is None


def test_old_state_migrates_without_changing_password_or_subscription(tmp_path):
    store = setup_state(tmp_path)
    del store.state["settings"]["selection_strategy"]
    del store.state["settings"]["primary_subscription_id"]
    store.save()
    before = json.loads(store.path.read_text(encoding="utf-8"))
    loaded = Store(tmp_path)
    assert loaded.state["settings"]["selection_strategy"] == "latency"
    assert loaded.state["subscriptions"] == before["subscriptions"]
    assert loaded.state["password_hash"] == before["password_hash"]


def test_deleting_primary_returns_to_global_selection(tmp_path):
    from test_manager import StubCore
    async def scenario():
        store = setup_state(tmp_path)
        await Manager(store,StubCore()).delete_subscription("main")
        assert store.state["settings"]["selection_strategy"] == "latency"
        assert store.state["settings"]["primary_subscription_id"] is None
    asyncio.run(scenario())
