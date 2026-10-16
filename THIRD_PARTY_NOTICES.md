# 第三方组件

本项目的管理页面、API、订阅整理和配置编排独立实现。

代理连接由 Mihomo 内核执行。Windows 安装脚本从官方发布页下载，Dockerfile 从官方镜像复制该内核。

- 项目：MetaCubeX/mihomo
- 已验证版本：v1.19.32
- 源代码：https://github.com/MetaCubeX/mihomo/tree/v1.19.32
- 发布页：https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.32
- 许可证：GPL-3.0，副本位于 `third_party\Mihomo-LICENSE.txt`

分发包含 Mihomo 的二进制或镜像时，需要遵循其许可条款并提供对应源码及必要信息。

Python 依赖的许可和版本信息保留在安装环境各包的 dist-info 目录中，可通过 `python -m pip show fastapi uvicorn httpx PyYAML` 查询。

## Heroicons

界面使用 Heroicons v2.2.0 的官方 SVG 图标，文件位于 `static\icons`。

- 项目：https://github.com/tailwindlabs/heroicons/tree/v2.2.0
- 许可证：MIT，副本位于 `third_party\Heroicons-LICENSE.txt`。
