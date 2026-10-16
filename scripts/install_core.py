"""Download the official mihomo release and verify the GitHub SHA-256 digest."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import platform
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "route-proxy-manager", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read(100 * 1024 * 1024 + 1)
    if len(content) > 100 * 1024 * 1024:
        raise ValueError("下载文件超过大小限制")
    return content


def main():
    parser = argparse.ArgumentParser(description="安装官方 Mihomo 内核")
    parser.add_argument("--version", default="v1.19.32", help="版本号，例如 v1.19.32；也可填写 latest")
    args = parser.parse_args()
    machine = platform.machine().lower()
    arch = "amd64-compatible" if machine in {"amd64", "x86_64"} else "arm64" if machine in {"arm64", "aarch64"} else None
    system = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    if not system or not arch:
        raise ValueError("当前平台请手动下载官方 Mihomo，并通过 MIHOMO_BIN 指定路径")
    release_path = "latest" if args.version == "latest" else "tags/" + args.version
    print("正在读取官方版本信息…", flush=True)
    release = json.loads(download("https://api.github.com/repos/MetaCubeX/mihomo/releases/" + release_path))
    extension = ".zip" if system == "windows" else ".gz"
    name = f"mihomo-{system}-{arch}-{release['tag_name']}{extension}"
    asset = next((a for a in release["assets"] if a["name"] == name), None)
    if not asset or not asset.get("digest", "").startswith("sha256:"):
        raise ValueError("该版本缺少适用文件或 SHA-256 校验信息，请手动核对官方发布页")
    print(f"正在下载 {name}…", flush=True)
    content = download(asset["browser_download_url"])
    if hashlib.sha256(content).hexdigest() != asset["digest"].split(":", 1)[1]:
        raise ValueError("SHA-256 校验失败，未安装下载文件")
    if extension == ".zip":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [n for n in archive.namelist() if n.lower().endswith(".exe")]
            if len(members) != 1:
                raise ValueError("压缩包内的内核文件不唯一")
            binary = archive.read(members[0])
    else:
        binary = gzip.decompress(content)
    target = ROOT / "bin" / ("mihomo.exe" if system == "windows" else "mihomo")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".download")
    temporary.write_bytes(binary)
    if system != "windows":
        temporary.chmod(0o755)
    os.replace(temporary, target)
    print(f"校验通过，已安装：{target}", flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as exc:
        print(f"安装失败：{exc}", file=sys.stderr)
        sys.exit(1)
