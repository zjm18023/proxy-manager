from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import yaml

REGIONS = {
    "HK": ("中国香港", r"香港|🇭🇰|hong\s*kong|(?<![a-z])hk(?![a-z])"),
    "MO": ("中国澳门", r"澳门|澳門|🇲🇴|macau|macao|(?<![a-z])mo(?![a-z])"),
    "TW": ("中国台湾", r"台湾|台灣|🇹🇼|taiwan|taipei|(?<![a-z])tw(?![a-z])"),
    "JP": ("日本", r"日本|东京|東京|大阪|🇯🇵|japan|tokyo|osaka|(?<![a-z])jp(?![a-z])|日本jp"),
    "SG": ("新加坡", r"新加坡|狮城|獅城|🇸🇬|singapore|(?<![a-z])sg(?![a-z])"),
    "US": ("美国", r"美国|美國|洛杉矶|硅谷|纽约|🇺🇸|united\s*states|america|los\s*angeles|(?<![a-z])us[0-9]*(?![a-z])|usa"),
    "KR": ("韩国", r"韩国|韓國|首尔|🇰🇷|korea|seoul|(?<![a-z])kr(?![a-z])"),
    "GB": ("英国", r"英国|英國|伦敦|🇬🇧|united\s*kingdom|london|(?<![a-z])(?:uk|gb)(?![a-z])"),
    "DE": ("德国", r"德国|德國|法兰克福|🇩🇪|germany|frankfurt|(?<![a-z])de(?![a-z])"),
    "FR": ("法国", r"法国|法國|巴黎|🇫🇷|france|paris|(?<![a-z])fr(?![a-z])"),
    "CA": ("加拿大", r"加拿大|🇨🇦|canada|toronto|(?<![a-z])ca(?![a-z])"),
    "AU": ("澳大利亚", r"澳大利亚|澳洲|悉尼|🇦🇺|australia|sydney|(?<![a-z])au(?![a-z])"),
    "RU": ("俄罗斯", r"俄罗斯|俄羅斯|🇷🇺|russia|moscow|(?<![a-z])ru(?![a-z])"),
    "IN": ("印度", r"印度(?!尼)|🇮🇳|india(?!na)|(?<![a-z])in(?![a-z])"),
    "NL": ("荷兰", r"荷兰|荷蘭|🇳🇱|netherlands|amsterdam|(?<![a-z])nl(?![a-z])"),
    "CN": ("中国大陆", r"中国大陆|中国(?!香港|澳门|台湾)|中國大陸|大陆|🇨🇳|china|(?<![a-z])cn(?![a-z])"),
    "OTHER": ("其他 / 未识别", r"(?!)"),
}
MAX_BYTES = 8 * 1024 * 1024


def detect_region(name: str) -> str:
    for code, (_, pattern) in REGIONS.items():
        if re.search(pattern, name, re.IGNORECASE):
            return code
    return "OTHER"


def decode64(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8-sig")


def parse_uri(line: str) -> dict:
    if line.startswith("vmess://"):
        data = json.loads(decode64(line[8:]))
        node = {"type": "vmess", "name": data.get("ps", data["add"]),
                "server": data["add"], "port": int(data["port"]),
                "uuid": data["id"], "alterId": int(data.get("aid", 0)), "cipher": data.get("scy", "auto")}
        if data.get("tls") in ("tls", True):
            node.update({"tls": True, "servername": data.get("sni") or data.get("host") or data["add"]})
        if data.get("net") not in (None, "", "tcp"):
            node["network"] = data["net"]
        if data.get("net") == "ws":
            node["ws-opts"] = {"path": data.get("path", "/"), "headers": {"Host": data.get("host", data["add"])}}
        if data.get("net") == "grpc":
            node["grpc-opts"] = {"grpc-service-name": data.get("path", "")}
        return node
    scheme, _, remainder = line.partition("://")
    if scheme == "ss" and "@" not in remainder.split("#")[0]:
        raw, _, label = remainder.partition("#")
        line = "ss://" + decode64(raw) + ("#" + label if label else "")
    parts = urlsplit(line)
    query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    kind = {"hy2": "hysteria2"}.get(scheme, scheme)
    if kind not in {"ss", "trojan", "vless", "hysteria2", "tuic"}:
        raise ValueError("暂不支持此节点链接类型，请使用 Clash / Mihomo 格式订阅")
    node = {"type": kind, "name": unquote(parts.fragment) or parts.hostname,
            "server": parts.hostname, "port": parts.port or 443}
    if kind == "ss":
        credentials = unquote(parts.netloc.rsplit("@", 1)[0])
        if ":" not in credentials:
            credentials = decode64(credentials)
        cipher, password = credentials.split(":", 1)
        node.update({"cipher": cipher, "password": password})
        if query.get("plugin"):
            plugin, *options = query["plugin"].split(";")
            node["plugin"] = {"simple-obfs": "obfs"}.get(plugin, plugin)
            opts = dict(opt.split("=", 1) if "=" in opt else (opt, True) for opt in options)
            node["plugin-opts"] = {k.removeprefix("obfs-"): v for k, v in opts.items()}
    elif kind in {"trojan", "hysteria2"}:
        node["password"] = unquote(parts.netloc.rsplit("@", 1)[0])
    else:
        node["uuid"] = unquote(parts.username or "")
        if kind == "tuic":
            node["password"] = unquote(parts.password or "")
            node["congestion-controller"] = query.get("congestion_control", "bbr")
    if kind == "vless":
        node["tls"] = query.get("security", "") in {"tls", "reality"}
        if query.get("flow"):
            node["flow"] = query["flow"]
        if query.get("security") == "reality":
            node["reality-opts"] = {"public-key": query.get("pbk", ""), "short-id": query.get("sid", "")}
        if query.get("fp"):
            node["client-fingerprint"] = query["fp"]
    sni = query.get("sni") or query.get("peer")
    if sni:
        node["sni" if kind in {"hysteria2", "tuic"} else "servername"] = sni
    if query.get("allowInsecure", query.get("insecure")) in {"1", "true"}:
        node["skip-cert-verify"] = True
    if query.get("alpn"):
        node["alpn"] = query["alpn"].split(",")
    transport = query.get("type", "tcp")
    if transport != "tcp":
        node["network"] = transport
    if transport == "ws":
        node["ws-opts"] = {"path": query.get("path", "/"), "headers": {"Host": query.get("host", node["server"])}}
    if transport == "grpc":
        node["grpc-opts"] = {"grpc-service-name": query.get("serviceName", "")}
    if kind == "hysteria2" and query.get("obfs"):
        node.update({"obfs": query["obfs"], "obfs-password": query.get("obfs-password", "")})
    return node


def parse_subscription(text: str) -> tuple[list[dict], list[str]]:
    text = text.lstrip("\ufeff").strip()
    if not text or len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("订阅内容为空或超过 8 MB")
    data = None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        pass
    warnings = []
    if isinstance(data, dict) and isinstance(data.get("proxies"), list):
        raw_nodes = data["proxies"]
    elif isinstance(data, list):
        raw_nodes = data
    else:
        if "://" not in text:
            try:
                text = decode64(text)
            except (ValueError, UnicodeError):
                raise ValueError("无法识别订阅内容，请选择 Clash / Mihomo 格式订阅") from None
        raw_nodes = []
        for index, line in enumerate(text.splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                raw_nodes.append(parse_uri(line.strip()))
            except (ValueError, KeyError, TypeError, UnicodeError):
                warnings.append(f"第 {index} 行节点格式不支持或不完整")
    nodes = []
    seen = set()
    for raw in raw_nodes:
        if not isinstance(raw, dict) or not raw.get("name") or not raw.get("server") or not raw.get("type"):
            warnings.append("已跳过缺少名称、服务器或类型的节点")
            continue
        try:
            port = int(raw.get("port", 0))
            if not 1 <= port <= 65535:
                raise ValueError()
        except (ValueError, TypeError):
            warnings.append("已跳过端口无效的节点")
            continue
        # A subscription is data: top-level rules, listeners, scripts and providers are never imported.
        raw = {**raw, "name": str(raw["name"]), "port": port, "server": str(raw["server"])}
        fingerprint = json.dumps(raw, sort_keys=True, ensure_ascii=False)
        if fingerprint not in seen:
            seen.add(fingerprint)
            nodes.append(raw)
    if not nodes:
        raise ValueError("订阅中没有可导入的节点；仅引用远程 providers 的配置请换成节点订阅链接")
    if len(nodes) > 5000:
        raise ValueError("单个订阅最多支持 5000 个节点")
    return nodes, warnings[:30]


def node_id(subscription_id: str, raw: dict) -> str:
    identity = [subscription_id, raw["name"], raw["type"], raw["server"], raw["port"]]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20]


async def fetch_subscription(url: str) -> tuple[list[dict], list[str]]:
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=True, timeout=25) as client:
            async with client.stream("GET", url, headers={"User-Agent": "clash.meta", "Accept": "*/*"}) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise ValueError("订阅内容超过 8 MB")
        return await asyncio.to_thread(parse_subscription, content.decode("utf-8-sig"))
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"订阅服务器返回 HTTP {exc.response.status_code}，请检查链接和有效期") from None
    except httpx.HTTPError:
        raise ValueError("无法连接订阅服务器，请检查网络或稍后重试") from None
    except UnicodeError:
        raise ValueError("订阅内容不是有效的 UTF-8 文本") from None
