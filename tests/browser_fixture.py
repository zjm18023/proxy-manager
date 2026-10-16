"""Isolated local fixture for browser QA; never starts with production data."""
import os
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from proxy_manager.app import create_app
from proxy_manager.store import Store


class Handler(BaseHTTPRequestHandler):
    broken = False
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        self.send_response(200)
        self.end_headers()
        self.close_connection = False

    def do_GET(self):
        if self.path == "/break":
            Handler.broken = True
            self.send_response(204)
            self.end_headers()
        elif self.path.startswith("/feed"):
            if Handler.broken:
                self.send_response(503)
                self.end_headers()
                return
            body = yaml.safe_dump({"proxies":[
                {"name":name, "type":"http", "server":"127.0.0.1", "port":self.server.server_port}
                for name in ["日本JP-A", "香港HK-A", "美国US-A", "新加坡SG-HY2", "韩国KR-A", "未识别专线"]
            ]}, allow_unicode=True).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(204)
            self.end_headers()

    do_HEAD = do_GET


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8790), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["PROXY_MANAGER_CORE_PORT"] = "19091"
    os.environ["PROXY_MANAGER_PORT"] = "8788"
    directory = Path(tempfile.mkdtemp(prefix="route-browser-qa-"))
    store = Store(directory)
    store.state["settings"].update(proxy_port=17891, test_url="http://probe.invalid/check")
    store.save()
    print("Isolated QA: http://127.0.0.1:8788 | fixture feed: http://127.0.0.1:8790/feed", flush=True)
    try:
        uvicorn.run(create_app(directory), host="127.0.0.1", port=8788)
    finally:
        server.shutdown()
        server.server_close()
