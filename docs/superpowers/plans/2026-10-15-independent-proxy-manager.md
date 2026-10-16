# Independent Proxy Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供独立于生图项目的中文代理管理页面，支持多个订阅、地区排除、节点检测与出口选择，供其他项目复用。

**Architecture:** Python 管理服务负责订阅、持久化、登录和配置编排，静态前端与服务同源。Mihomo 作为受管理的子进程，仅在本机开放控制接口，统一提供 HTTP / SOCKS5 入口。订阅只导入节点，更新先验证后应用，失败保留此前节点。

**Tech Stack:** Python 3.11+、FastAPI、HTTPX、PyYAML、原生 HTML/CSS/JavaScript、Mihomo v1.19.32。

**Spec:** 项目根目录 `README.md` 对应用户已确认的独立部署、多订阅、地区筛选、节点检测、出口切换和统一代理入口需求。本计划与代码一并交付，执行已由用户“好的，帮我实现吧”授权。

## Global Constraints

- 所有生成的文本文件使用 UTF-8 无 BOM；Windows 10 可运行。
- 独立目录 `H:\GPTshengtu2.0\proxy-manager`，无需修改或依赖生图应用。
- 页面文案面向使用者，配置与私人订阅仅保存在本服务的数据目录。
- 代理默认端口 7891，管理页面 8787，内核控制接口仅监听本机 9091。
- 没有候选节点时 REJECT；订阅失败不清空上次成功缓存。

## Review Focus

- 同名节点跨订阅可区分，ID 不因节点密码更新而改变。
- 失效订阅、空订阅、无效内核配置不能覆盖上次可用节点。
- 所有节点被排除时，实际代理请求不能改为直连。
- 地区识别允许手动修正，排除或删除手动出口时切回自动模式。
- 订阅链接和控制密钥不能泄漏到公开接口、日志列表或静态目录；变更需登录并校验来源。

## Task 1: 订阅、节点与配置

**Files:** `proxy_manager\store.py`、`proxy_manager\subscriptions.py`、`proxy_manager\core.py`、`proxy_manager\service.py`。

**Interfaces:** `parse_subscription(text) -> (nodes, warnings)`；`inventory(state) -> list[dict]`；`build_config(state, controller_port) -> str`；`Manager.refresh(identifiers=None)`。

- [x] 为 YAML / Base64 订阅、地区识别、跨订阅同名节点、无效输入编写行为测试。
- [x] 实现原子保存、缓存、定时更新、候选筛选及手动节点设置。
- [x] 用 `mihomo -t` 验证候选配置，再通过内核接口应用；失败时撤销修改。
- [x] 验证单个订阅失败保留缓存，不影响其他订阅更新。

验证：`python -m pytest tests\test_subscriptions.py tests\test_manager.py -q`。

## Task 2: 独立管理 API 与登录

**Files:** `proxy_manager\app.py`、`run.py`。

**Interfaces:** `/api/auth`、`/api/login`、`/api/state`、`/api/subscriptions`、`/api/settings`、`/api/nodes/{id}`、`/api/select`、`/api/test`、`/api/core/restart`。

- [x] 实现首次密码设置、PBKDF2 哈希、HttpOnly Cookie、会话过期、退出与登录限流。
- [x] 保护订阅和变更接口，拒绝跨源变更请求，返回中文校验提示。
- [x] 代理允许跨机器访问时要求认证和 IP 允许列表。
- [x] 验证未授权请求、退出撤销、敏感字段保护和输入校验。

验证：`python -m pytest tests\test_api.py -q`。

## Task 3: 中文管理页面

**Files:** `static\index.html`、`static\network.css`、`static\app.js`、`static\effects.js`。

- [x] 实现订阅添加、编辑、删除、更新以及失败原因展示。
- [x] 实现分页节点列表、名称搜索、来源与状态筛选、单节点管理、检测和出口切换。
- [x] 实现地区复选、关键词筛选、连接设置和活动记录。
- [x] 检查桌面与 390px 手机布局、登录、弹窗、操作反馈和浏览器异常。

验证：`node --check static\app.js`；独立临时数据运行 `python tests\browser_fixture.py` 后使用浏览器完成全流程。截图位于 `docs\screenshots`，其中订阅是本地测试样例。

## Task 4: Windows 启动与实际代理验证

**Files:** `scripts\install_core.py`、`start.ps1`、`start.bat`、`Dockerfile`、`compose.yaml`、`README.md`、`tests\test_real_core.py`。

- [x] 从官方发布页下载内核，并核验 SHA-256；支持已有内核路径。
- [x] Windows PowerShell 5.1 启动脚本创建独立环境、安装依赖并启动服务。
- [x] 用真实内核验证更新、地区排除、手动出口、代理请求、认证、无效配置保留与全部排除拒绝连接。
- [x] 提供 Linux Docker 部署配置及文档；当前机器未安装 Docker，容器启动留待目标服务器验证。

验证：`python -m pytest tests -q`；启动 `start.ps1` 后访问 `http://127.0.0.1:8787`。正式数据不包含测试订阅，也未替用户设置管理密码。
