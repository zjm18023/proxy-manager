from __future__ import annotations

import copy
import json
import os
import secrets
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.path = self.directory / "state.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            self.state["settings"].setdefault("selection_strategy", "latency")
            self.state["settings"].setdefault("primary_subscription_id", None)
        else:
            self.state = {
                "password_hash": None,
                "controller_secret": secrets.token_urlsafe(36),
                "subscriptions": [],
                "overrides": {},
                "selected": "AUTO",
                "settings": {
                    "excluded_regions": [], "exclude_keywords": "",
                    "refresh_hours": 24, "test_url": "https://cp.cloudflare.com/generate_204",
                    "test_interval": 300, "proxy_port": 7891, "allow_lan": False,
                    "proxy_username": "", "proxy_password": "",
                    "allowed_ips": ["127.0.0.0/8", "::1/128"],
                    "selection_strategy": "latency", "primary_subscription_id": None,
                },
            }
            self.save()

    def snapshot(self) -> dict:
        return copy.deepcopy(self.state)

    def save(self) -> None:
        atomic_write(self.path, json.dumps(self.state, ensure_ascii=False, indent=2))
