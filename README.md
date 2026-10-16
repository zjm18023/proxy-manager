# 红嘴鸥代理服务

一个独立运行的中文代理管理工具：添加自己的订阅、筛选地区、检测节点、自动选择出口，或设置主订阅与备用订阅，为应用提供固定的 HTTP / SOCKS5 代理入口。

**需要自行准备可用的代理订阅，本项目不提供节点。** 不需要安装 Node.js、数据库或编译前端。

## 从哪里开始

| 你的使用场景 | 阅读章节 |
| --- | --- |
| 在 Windows 10 电脑上给自己的应用使用 | [Windows 本地运行](#windows) |
| 在服务器上长期运行，方便更新和迁移 | [Docker 部署](#docker) |
| 已使用宝塔，希望在面板中管理进程 | [宝塔 Python 项目部署](#baota) |
| 已经启动，需要添加订阅和连接应用 | [首次使用](#first-use) |
| 更新、备份或排查故障 | [日常维护](#maintenance)、[常见问题](#faq) |

宝塔服务器也可以安装 Docker，直接采用 Docker 路线。**Docker 和宝塔 Python 项目二选一，不要同时启动同一份数据。**

### 管理地址和代理地址有什么区别

| 用途 | 本机默认地址 | 使用位置 |
| --- | --- | --- |
| 管理页面 | `http://127.0.0.1:8787` | 浏览器中添加订阅、切换节点和修改设置 |
| HTTP 代理 | `http://127.0.0.1:7891` | 填入需要使用代理的应用 |
| SOCKS5 代理 | `socks5://127.0.0.1:7891` | 应用支持 SOCKS5 时使用 |

HTTP 和 SOCKS5 共用 `7891` 端口。`8787` 不能当作代理端口。`127.0.0.1` 始终表示当前机器，应用在另一台机器或另一个容器里时，需要另行配置访问地址。

<a id="windows"></a>

## 一、Windows 本地运行

### 1. 准备环境和项目文件

安装 **Python 3.11+，推荐 Python 3.12**，安装时勾选添加到 PATH。在新开的 PowerShell 中运行 `python --version`，确认能显示版本号。也可以使用已安装的 `uv`，启动脚本会通过它创建 Python 3.12 环境。

已有项目文件时无需再次下载。如果需要从 GitHub 下载，安装 Git 后执行：

```powershell
Set-Location -LiteralPath 'H:\GPTshengtu2.0'
git clone https://github.com/zjm18023/proxy-manager.git
```

也可以在仓库页面选择 **Code → Download ZIP**，解压后使用。下文以 `H:\GPTshengtu2.0\proxy-manager` 为例，存放在其他位置时替换为自己的路径。

### 2. 启动并打开页面

最简单的方法是 **双击项目根目录的 `start.bat`**，也可以在 PowerShell 中执行：

```powershell
Set-Location -LiteralPath 'H:\GPTshengtu2.0\proxy-manager'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

首次启动会创建 `.venv`、安装 Python 依赖、下载并校验 Mihomo v1.19.32 内核，然后启动管理服务。首次准备需要联网，等待窗口出现 `Uvicorn running on http://127.0.0.1:8787`。

浏览器打开 **http://127.0.0.1:8787**，按[首次使用](#first-use)配置密码和订阅。运行期间保留启动窗口；停止时按 **Ctrl+C**，再次运行 `start.bat` 即可重新启动。

### 3. 手动启动与内核下载失败处理

希望逐步确认安装结果时，可以依次执行以下命令；上一步成功后再继续：

```powershell
Set-Location -LiteralPath 'H:\GPTshengtu2.0\proxy-manager'
$env:PYTHONUTF8 = '1'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe .\scripts\install_core.py --version v1.19.32
.\.venv\Scripts\python.exe .\run.py
```

不需要激活虚拟环境。直接执行 `run.py` 不会自动安装依赖或下载内核，前两项准备需要先完成。

如果 GitHub API 限流或下载失败，可从 [Mihomo 官方发布页](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.32)下载匹配系统架构的 Windows 压缩包，核对官方校验信息后解压，将可执行文件放到 `bin\mihomo.exe`。也可以指定已有内核：

```powershell
$env:MIHOMO_BIN = 'D:\tools\mihomo\mihomo.exe'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -SkipCoreDownload
```

管理页面能打开，但提示尚未安装内核时，仍需完成内核安装才能使用代理。

<a id="first-use"></a>

## 二、首次使用：添加订阅并连接应用

1. 打开管理页面，设置至少 **10 位**的管理密码，以后使用同一密码登录。
2. 在“订阅管理”添加订阅名称和链接，点击“更新全部”，等待节点导入成功。
3. 在“地区与筛选”排除不需要的地区或关键词。
4. 在“全部节点”检测候选节点，保持自动选择，或点击某个节点的“使用”固定出口。
5. 将代理地址填入自己的应用。本机默认使用 `http://127.0.0.1:7891` 或 `socks5://127.0.0.1:7891`。

应用如果分开填写主机、端口和协议，主机填 `127.0.0.1`，端口填 `7891`，协议按应用支持的类型选择。**管理密码用于登录页面；代理账号和密码在“连接设置”中另行设置。**

### 订阅和选路说明

- 优先使用 Clash / Mihomo YAML；也支持常见 SS、VMess、VLESS、Trojan、Hysteria2、TUIC 链接及 Base64 链接列表。复杂传输参数建议使用 Mihomo YAML。
- 最多 50 个订阅，每个订阅最多 8 MB / 5000 个节点。单个订阅更新失败时保留原缓存，其他订阅可继续更新。
- “全部订阅择优”会从筛选后的候选节点中按检测结果选择出口。“更多 → 设为主订阅”可开启主订阅优先，主订阅不可用时使用其他订阅作为备用。
- 主线路恢复需要连续通过两轮检测才回切。默认每 300 秒检测一次、单次超时 5 秒、HTTP 200 / 204 为成功；检测间隔最低可设为 60 秒。页面刷新不等于重新检测。
- 手动选中节点后保持固定，重新开启自动选择才恢复自动切换。切换影响新连接，已有长连接可能继续使用原出口。
- 检测成功不保证特定业务接受该出口。全部候选节点不可用时请求可能失败，不会改为直连。多个应用共用入口时，也共用当前出口。

<a id="docker"></a>

## 三、Docker 部署

### 1. 准备 Docker 和项目文件

需要 Docker Engine 和 **Docker Compose v2**。Windows 使用支持当前 Windows 10 / WSL2 环境的 Docker Desktop，并启用 Linux 容器。先确认：

```text
docker --version
docker compose version
```

将项目克隆或解压到部署机器，进入含 `compose.yaml` 的项目根目录。Windows 可以直接使用前面的项目目录；Linux 服务器可执行：

```bash
git clone https://github.com/zjm18023/proxy-manager.git
cd proxy-manager
```

以下 Docker 命令在 Windows PowerShell 和 Linux 终端中都可以使用。

### 2. 构建并启动

```text
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 proxy-manager
```

构建时会下载 Python、Mihomo 镜像及 Python 依赖。内核已包含在构建后的镜像中，不需要在容器内再次运行下载脚本。确认容器持续运行、日志无启动错误后，在部署机器浏览器打开 **http://127.0.0.1:8787**。

默认只将 `8787`、`7891` 映射到宿主机回环地址，**直接访问 `http://服务器IP:8787` 不会打开页面**。远程服务器首次配置时，在自己的 Windows PowerShell 中建立 SSH 隧道：

```powershell
ssh -N -L 18787:127.0.0.1:8787 服务器用户名@服务器IP
```

替换实际用户名和 IP，保持窗口打开，浏览器访问 **http://127.0.0.1:18787**，按[首次使用](#first-use)配置密码和订阅。需要域名访问时，参照[域名和 HTTPS](#domain)。

### 3. 配置代理监听和认证

**端口映射还需要容器内代理接受来自容器外的连接。** 在“连接设置 → 其他机器访问与认证”中：

1. 开启“允许其他机器连接此代理”。
2. 设置代理账号和至少 10 位的代理密码。
3. 填写允许访问的 IP / 网段。经过 Docker 转发后，来源地址可能是 Docker 网桥网关或网段，要按实际网络填写。
4. 保存设置，再向应用填写代理主机、端口、账号和密码。

查看实际网关和网段，可在宿主机执行 `docker network ls`，找到本项目容器所在网络，再执行 `docker network inspect 网络名称`，查看 `IPAM.Config` 中的 `Gateway` 和 `Subnet`。不要照抄其他机器上的网段。

| 应用所在位置 | 如何连接 |
| --- | --- |
| Docker 宿主机 | 使用 `127.0.0.1:7891`，填写页面设置的代理认证信息 |
| 另一台机器 | 将 `compose.yaml` 中代理端口的宿主机绑定地址改为指定内网 IP；同时配置防火墙、云安全组和页面允许列表 |
| 另一个容器 | 加入同一 Docker 网络，使用 `proxy-manager:7891`；允许其来源地址或网段 |

应用容器里的 `127.0.0.1` 指向它自己，不能直接拿来访问本项目容器。管理端口 `8787` 可以继续只映射到宿主机回环地址。

### 4. 数据持久化

Compose 使用命名卷 `proxy-manager-data`，挂载到容器内 `/app/data`；实际卷名通常带 Compose 项目前缀。宿主机项目目录里的 `data` **不会自动导入该卷**。

重启、重建镜像或执行不带 `-v` 的 `docker compose down` 会保留数据。**`docker compose down -v` 会删除数据卷，不要作为日常停止命令。** 更改 Compose 项目名或目录后，要确认仍使用原数据卷。

<a id="baota"></a>

## 四、宝塔 Python 项目部署

本节适用于 **Linux 宝塔面板**。标为 Bash 的命令在服务器终端执行，Windows 本地仍使用前面的 PowerShell 命令。宝塔如果选择 Docker 部署，直接按上一节操作。

### 1. 准备项目、虚拟环境和内核

1. 安装宝塔的 Python 项目管理功能；需要域名访问时同时安装 Nginx。
2. 上传或克隆项目到 `/www/wwwroot/proxy-manager`，确认该目录直接包含 `run.py`、`requirements.txt`、`proxy_manager` 和 `static`。
3. 在 Python 环境管理中安装 **Python 3.12**，创建独立的项目虚拟环境，记录面板显示的 Python 可执行文件完整路径。

下面的 `<Python路径>` 需要替换为虚拟环境中 `bin/python` 的真实完整路径，并保留引号。不要使用宝塔面板自身的 Python 环境。

```bash
cd /www/wwwroot/proxy-manager
"<Python路径>" -m pip install -r requirements.txt
"<Python路径>" scripts/install_core.py --version v1.19.32
./bin/mihomo -v
```

Linux 会安装内核到 `bin/mihomo`，不能使用 Windows 的 `mihomo.exe`。自动下载失败时，从官方发布页下载与服务器架构匹配的 Linux 压缩包，核对后解压到该路径，再执行 `chmod 755 /www/wwwroot/proxy-manager/bin/mihomo`。

### 2. 设置权限并在面板添加项目

以宝塔常见的 `www` 用户为例，在服务器终端准备数据目录。使用其他运行用户时，相应替换用户和用户组：

```bash
mkdir -p /www/wwwroot/proxy-manager/data
chown -R www:www /www/wwwroot/proxy-manager/data
chmod 700 /www/wwwroot/proxy-manager/data
```

运行用户还需能读取项目和虚拟环境、执行 Python 及 Mihomo。如果虚拟环境所在目录不允许该用户访问，应换用可访问的环境或修正权限。

在“网站 → Python 项目”或相应 Python 项目管理页面添加项目，具体菜单名称随面板版本变化：

| 配置项 | 填写内容 |
| --- | --- |
| 项目名称 | `proxy-manager` |
| 项目目录 / 工作目录 | `/www/wwwroot/proxy-manager` |
| Python 环境 | 刚创建的独立虚拟环境 |
| 运行方式 | 自定义命令 / command |
| 启动命令 | `"<Python路径>" -u /www/wwwroot/proxy-manager/run.py`，替换真实 Python 路径 |
| 项目端口 | `8787` |
| 运行用户 | `www` 或具有所需权限的专用用户 |
| 依赖文件 | `/www/wwwroot/proxy-manager/requirements.txt` |
| 自动启动 / 进程守护 | 开启 |

在面板提供的项目环境变量字段中添加：

```text
PYTHONUTF8=1
PYTHONUNBUFFERED=1
PROXY_MANAGER_HOST=127.0.0.1
PROXY_MANAGER_PORT=8787
PROXY_MANAGER_CORE_PORT=9091
PROXY_MANAGER_DATA=/www/wwwroot/proxy-manager/data
MIHOMO_BIN=/www/wwwroot/proxy-manager/bin/mihomo
```

仅在根目录放置 `.env` 文件不会被 `run.py` 自动读取，需要通过面板加载或填写上述变量。

**保持单进程运行，不要配置多个 worker、多个实例或自动重载。** 多个进程会争用内核端口和数据文件。管理服务会启动和关闭 Mihomo，无需单独为内核创建守护进程。

### 3. 启动并验证

等待依赖和环境准备完成，再在面板启动项目。除了查看“运行中”状态，还要检查项目日志和实际端口：

```bash
curl -i http://127.0.0.1:8787/api/auth
ss -lntp | grep -E ':(8787|7891|9091)\b'
```

认证接口应返回 HTTP 200；新数据目录的 `initialized` 为 `false`，设置密码后为 `true`。内核正常时应有代理和控制端口监听，也可从页面查看内核状态。

首次设置密码可使用 Docker 章节中的 SSH 隧道方法。服务器本机应用默认使用 `127.0.0.1:7891`；其他机器连接代理时，要在页面开启跨机器访问、设置认证及允许列表，并按需放行代理端口。

### 4. 绑定域名

将域名解析到服务器，在项目设置中绑定域名，并开启“外网映射”或“反向代理”，上游填写 `http://127.0.0.1:8787`。仅绑定域名而未开启映射，通常无法访问页面。申请证书并启用 HTTPS，反代配置要点见下一节。

<a id="domain"></a>

## 五、域名和 HTTPS

域名用于管理页面，应用使用的代理入口仍单独配置。不要将 Mihomo 控制端口 `9091` 映射到公网。

### 宝塔 Python 项目或宿主机直接运行

确认 Nginx 中处理 `/` 的反向代理包含以下配置。已有 `location /` 时修改原配置，不要重复添加；证书及站点的其他配置由宝塔管理：

```nginx
location / {
    proxy_pass http://127.0.0.1:8787;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

保存并确认 Nginx 配置检查通过，再访问自己的 HTTPS 域名。管理服务保持监听 `127.0.0.1:8787`，对外开放 Nginx 的 80 / 443 端口即可。

### 宿主机 Nginx 反代 Docker

上游仍是 `http://127.0.0.1:8787`，但请求到容器后，来源通常变为 Docker 网桥网关。默认 `run.py` 只信任 `127.0.0.1` 的转发头，HTTPS 访问还需要指定可信反向代理来源。

按 Docker 章节查出网络的 `Gateway`，在 `compose.yaml` 同级创建 UTF-8 编码的 `compose.override.yaml`。**将下面示例 `172.18.0.1` 替换为实际网关 IP**：

```yaml
services:
  proxy-manager:
    command:
      - python
      - -m
      - uvicorn
      - proxy_manager.app:create_app
      - --factory
      - --host
      - 0.0.0.0
      - --port
      - "8787"
      - --proxy-headers
      - --forwarded-allow-ips
      - "172.18.0.1"
```

执行 `docker compose config` 检查合并结果，再执行 `docker compose up -d` 应用。该方式仍只启动一个管理进程，由它管理 Mihomo。

宿主机端口映射保持 `127.0.0.1:8787:8787`。网络变化后重新核对实际来源，不要将可信地址直接设置为 `*`。这样 HTTPS 协议、登录 Cookie 和页面提交的来源检查才能正确工作。

<a id="maintenance"></a>

## 六、日常维护

### 启停和日志

| 操作 | Windows 本地 | Docker Compose | 宝塔 Python 项目 |
| --- | --- | --- | --- |
| 启动 | 双击 `start.bat` | `docker compose up -d` | 面板点击“启动” |
| 停止 | 启动窗口按 Ctrl+C | `docker compose stop` | 面板点击“停止” |
| 重启 | 停止后重新启动 | `docker compose restart` | 面板点击“重启” |
| 查看状态 | 启动窗口及管理页面 | `docker compose ps` | 项目进程、端口及管理页面 |
| 管理服务日志 | 启动窗口 | `docker compose logs -f --tail=100 proxy-manager` | 面板项目日志 |
| 内核日志 | `data\core\runtime.log` | `docker compose exec proxy-manager tail -n 100 /app/data/core/runtime.log` | 数据目录的 `core/runtime.log` |

Docker 配置了 `restart: unless-stopped`，Docker 服务重启后会恢复未主动停止的容器。手动 `stop` 后需要再执行 `up -d`；开机恢复还依赖 Docker 服务本身已设置开机启动。

### 更新项目

更新前先备份数据，确认没有需要保留的未提交源码修改。以下 Git 命令适用于 `git clone` 获取的项目；ZIP 下载的项目需重新下载程序文件，保留自己的数据、内核和部署配置。

- **Windows**：停止服务，执行 `git pull --ff-only`，重新运行 `start.bat`。依赖文件变化时，启动脚本会重新安装依赖。
- **宝塔 Python 项目**：面板停止项目，执行 `git pull --ff-only`，用项目虚拟环境重新安装 `requirements.txt`，再从面板启动。
- **Docker**：在项目目录依次执行：

```text
git pull --ff-only
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 proxy-manager
```

单纯 `docker compose restart` 不会将修改后的源码构建进镜像。更新后浏览器按 **Ctrl+F5** 刷新。`docs`、本地数据、虚拟环境和下载的内核不会随仓库提交。

### 备份与恢复

完整数据目录包含 `state.json` 和 `core`。订阅、节点缓存、筛选设置、管理密码哈希、代理认证信息均需妥善备份，避免上传到仓库。

**Windows 本地备份**：停止服务后，在项目目录的 PowerShell 执行，备份保存在项目外：

```powershell
New-Item -ItemType Directory -Path '..\proxy-manager-backups' -Force | Out-Null
$backupPath = Join-Path '..\proxy-manager-backups' ('data-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Copy-Item -LiteralPath '.\data' -Destination $backupPath -Recurse
```

**宝塔备份**：面板停止项目，备份实际数据目录，例如 `/www/wwwroot/proxy-manager/data`。恢复时先停止项目，将备份恢复到该目录，确认运行用户仍有读写权限，再启动。

**Docker 备份**：数据在命名卷中，不能只备份项目文件夹。在项目目录执行以下 PowerShell 命令，逐条确认成功，尤其确认备份中存在 `state.json`：

```powershell
New-Item -ItemType Directory -Path '..\proxy-manager-backups' -Force | Out-Null
$backupPath = Join-Path '..\proxy-manager-backups' ('data-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
docker compose stop
docker compose cp proxy-manager:/app/data $backupPath
docker compose start
```

Linux 宿主机可先创建项目外的备份目录，再同样使用 `docker compose cp proxy-manager:/app/data 备份目录` 复制。

Docker 恢复时，先停止容器并另行备份当前数据，再执行 `docker compose cp 备份数据目录/. proxy-manager:/app/data`，把目录内容复制回卷内。修正写权限后启动：

```text
docker compose run --rm --no-deps --user root --entrypoint sh proxy-manager -c "chown -R route:route /app/data"
docker compose start
```

恢复到全新机器时，需要先创建容器和数据卷，再停止容器复制备份。不同运行方式的数据不会自动迁移或合并。

### 更新内核、重置密码

- Windows / 宝塔：停止项目，用项目虚拟环境执行 `scripts/install_core.py --version v1.19.32`，再启动。也支持 `--version latest`，升级前应确认兼容性。
- Docker：内核版本由 `Dockerfile` 的 `metacubex/mihomo` 镜像标签指定，调整后重新构建；本地下载的内核不会被打包进镜像。
- 忘记管理密码：停止项目并备份数据，将实际数据目录的 `state.json` 中 `password_hash` 改为 `null`，保留其他内容，以 UTF-8 无 BOM 保存。再启动并从本机或 SSH 隧道设置新密码。Docker 需修改卷内数据，不要删除整个 `state.json`。

<a id="settings"></a>

## 七、端口和环境变量

| 默认端口 | 用途 | 默认访问范围 |
| --- | --- | --- |
| `8787` | 管理页面及 API | 本机；Docker 宿主机仅映射回环地址 |
| `7891` | HTTP / SOCKS5 混合代理 | 本机；跨机器连接需开启监听、认证及允许列表 |
| `9091` | 管理服务连接 Mihomo 的控制接口 | 本机，无需对外开放 |

| 环境变量 | 直接运行时的默认值 | 用途 |
| --- | --- | --- |
| `PROXY_MANAGER_HOST` | `127.0.0.1` | 管理页面监听地址 |
| `PROXY_MANAGER_PORT` | `8787` | 管理页面端口 |
| `PROXY_MANAGER_CORE_PORT` | `9091` | 内核控制端口 |
| `PROXY_MANAGER_DATA` | 项目根目录的 `data` | 数据目录，服务器建议使用绝对路径 |
| `MIHOMO_BIN` | 优先项目 `bin` 中对应系统的内核，再查找 PATH | 自定义内核完整路径 |

Docker 镜像已将管理监听设为 `0.0.0.0`、数据目录设为 `/app/data`、内核设为 `/usr/local/bin/mihomo`。覆盖启动命令时需确认参数与端口映射一致。

Windows 临时更换管理端口，在同一个 PowerShell 窗口执行：

```powershell
$env:PROXY_MANAGER_PORT = '8788'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

管理页面监听 `0.0.0.0` 时接受其他机器连接，还需要配置对应网络访问范围；它与页面中的代理跨机器访问开关分别生效。代理端口和认证在“连接设置”中修改。Docker 若改变容器内代理端口，需同步调整端口映射并重新创建容器。

<a id="faq"></a>

## 八、常见问题

| 现象 | 优先检查 |
| --- | --- |
| Windows 找不到 Python | 安装 Python 3.11+ 并加入 PATH，重开终端确认 `python --version` |
| PowerShell 禁止运行脚本 | 使用 `start.bat` 或前述 `powershell.exe -ExecutionPolicy Bypass` 命令 |
| 下载内核失败 | 检查 GitHub 连接；API 限流时从官方发布页手动下载 |
| 页面能打开，代理不可用 | 内核是否安装、订阅是否成功导入、是否有可用候选节点；再查内核日志 |
| Docker 宿主机连接 7891 失败 | 容器内是否开启跨机器连接、设置认证，并允许实际 Docker 来源网段 |
| 另一台机器打不开页面 | 默认仅本机访问；使用 SSH 隧道或配置域名反代，核对网络及端口 |
| 宝塔域名返回 502 | 先访问服务器的 `http://127.0.0.1:8787/api/auth`，再查进程、端口和 Nginx 上游 |
| HTTPS 页面提示“请从管理页面内进行操作” | 检查 Host、X-Forwarded-Proto，以及 Docker 的可信转发来源配置 |
| 端口被占用 | 检查 8787、7891、9091，关闭重复实例或更换端口 |
| Permission denied | 数据目录写权限、Python 和内核执行权限；Docker 恢复数据后检查属主 |
| 更新后仍是旧页面 | 确认启动目录；Docker 需重建镜像，浏览器再按 Ctrl+F5 |
| 重启后订阅消失或要重新设置密码 | 核对数据目录、Compose 项目名和数据卷，找到原数据后恢复 |

节点地区按名称推断，可在页面修正，不等于真实出口 IP 定位。界面动效可在右上角关闭，地区球体和连接路径是状态示意，不表示实时流量或真实物理路径。

## 第三方组件

代理连接由 [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo) 执行，默认已验证版本为 v1.19.32，许可证为 GPL-3.0。Heroicons 图标使用 MIT 许可证。

详细说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，许可证副本保存在 `third_party`。分发含 Mihomo 的二进制或镜像时，需要遵循对应许可并提供所需源码信息。
