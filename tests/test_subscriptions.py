import base64

import pytest

from proxy_manager.subscriptions import detect_region, node_id, parse_subscription


@pytest.mark.parametrize("name,region", [
    ("日本JP-A", "JP"), ("HK 01", "HK"), ("Hong Kong Premium", "HK"),
    ("🇺🇸 Los Angeles", "US"), ("新加坡SG-HY2", "SG"), ("美国-01", "US"),
    ("印度尼西亚", "OTHER"), ("业务专线", "OTHER"), ("Macau", "MO"),
])
def test_region_names(name, region):
    assert detect_region(name) == region


def test_yaml_only_imports_nodes_and_deduplicates():
    nodes, warnings = parse_subscription("""
mixed-port: 12345
rules: [MATCH,DIRECT]
proxies:
  - {name: 日本, type: http, server: localhost, port: 8888}
  - {name: 日本, type: http, server: localhost, port: 8888}
  - {name: 无效, type: http, server: localhost, port: 99999}
""")
    assert len(nodes) == 1
    assert "mixed-port" not in nodes[0]
    assert warnings


def test_base64_subscription_preserves_credentials_and_ipv6():
    text = "trojan://p%40ss@[::1]:443?sni=example.org#%E6%97%A5%E6%9C%AC\nvless://uuid@node.example:443?security=reality&pbk=public&sid=12&fp=chrome#US"
    nodes, warnings = parse_subscription(base64.urlsafe_b64encode(text.encode()).decode().rstrip("="))
    assert not warnings
    assert nodes[0]["server"] == "::1"
    assert nodes[0]["password"] == "p@ss"
    assert nodes[0]["name"] == "日本"
    assert nodes[1]["reality-opts"]["public-key"] == "public"


@pytest.mark.parametrize("text", ["<html>Login required</html>", "proxies: []", "", "proxy-providers: {remote: {type: http}}"])
def test_empty_or_unusable_subscription_is_not_success(text):
    with pytest.raises(ValueError):
        parse_subscription(text)


def test_same_names_in_different_subscriptions_have_distinct_ids():
    raw = {"name":"日本", "type":"http", "server":"localhost", "port":8080}
    assert node_id("one", raw) != node_id("two", raw)
    assert node_id("one", raw) == node_id("one", raw | {"password":"changed"})
