"""Primary subscription preference based on actual per-URL probe results."""
from __future__ import annotations

from datetime import datetime


class PrimaryPreference:
    required_successes = 2

    def __init__(self):
        self.key = None
        self.needs_recovery = False
        self.recovery_since: float | None = None
        self.successes = 0

    def choose(self, settings: dict, subscriptions: list[dict], nodes: list[dict], live: dict) -> dict:
        primary_id, url = settings["primary_subscription_id"], settings["test_url"]
        key = (primary_id, url)
        if self.key != key:
            self.__init__()
            self.key = key
        candidates = [node for node in nodes if node["eligible"]]
        primary = [node for node in candidates if node["subscription_id"] == primary_id]
        backup = [node for node in candidates if node["subscription_id"] != primary_id]

        def probe(node):
            result = live.get(node["core_name"], {}).get("extra", {}).get(url, {})
            history = result.get("history", [])
            if not history:
                return None, None
            last = history[-1]
            healthy = result.get("alive", bool(last.get("delay"))) and bool(last.get("delay"))
            try:
                checked = datetime.fromisoformat(last["time"].replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError):
                checked = None
            return healthy, checked

        primary_probes = [probe(node) for node in primary]
        backup_healthy = any(probe(node)[0] for node in backup)
        primary_healthy = any(healthy for healthy, _ in primary_probes)
        previous = live.get("AUTO", {}).get("now", "REJECT")
        primary_source = next((s for s in subscriptions if s["id"] == primary_id), None)
        branch, reason = "REJECT", "all_unavailable"
        if primary_healthy:
            if self.needs_recovery and previous != "PRIMARY" and backup_healthy:
                samples = [checked for healthy, checked in primary_probes if healthy and checked is not None]
                latest = max(samples) if samples else None
                if latest is not None:
                    if self.recovery_since is None:
                        self.recovery_since, self.successes = latest, 1
                    elif latest - self.recovery_since >= settings["test_interval"] * .95:
                        self.successes = self.required_successes
                if self.successes < self.required_successes:
                    branch, reason = "BACKUP", "primary_recovering"
                else:
                    branch, reason = "PRIMARY", "primary_healthy"
                    self.needs_recovery = False
            else:
                # An available primary is preferable to rejecting traffic while backup is down.
                branch, reason = "PRIMARY", "primary_healthy"
                self.needs_recovery = False
        else:
            self.recovery_since, self.successes = None, 0
            if previous == "PRIMARY" or primary_probes and all(healthy is False for healthy, _ in primary_probes):
                self.needs_recovery = True
            unknown = any(healthy is None for healthy, _ in primary_probes)
            if backup_healthy:
                branch = "BACKUP"
                reason = "primary_checking" if unknown else "primary_unavailable"
                if not primary_source or not primary_source["enabled"]:
                    reason = "primary_disabled"
                elif not primary:
                    reason = "primary_filtered"
            elif unknown or any(probe(node)[0] is None for node in backup):
                reason = "checking"
        return {"branch":branch, "reason":reason, "recovery_successes":self.successes,
                "required_successes":self.required_successes,
                "primary_candidates":len(primary), "backup_candidates":len(backup)}


def resolve_node(live: dict, group: str = "OUTBOUND") -> str | None:
    """Resolve selectors and nested probe groups without assuming a fixed depth."""
    visited = set()
    current = group
    while current and current not in visited:
        visited.add(current)
        target = live.get(current, {}).get("now")
        if not target:
            return current
        current = target
    return None
