import os

import uvicorn

from proxy_manager.app import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host=os.environ.get("PROXY_MANAGER_HOST", "127.0.0.1"),
                port=int(os.environ.get("PROXY_MANAGER_PORT", "8787")),
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")
