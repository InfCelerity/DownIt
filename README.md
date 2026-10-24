# DownIt

Minecraft 下载器：从 Mojang CDN 拉取游戏本体，并自动完成 **Fabric / Forge / NeoForge / OptiFine** 的安装与修补。

后端只依赖 Python 标准库，前端用 React + Fluent UI，接口与界面打包在一起，可生成单文件 exe。支持官方源与中国大陆镜像自动测速切换。

## 功能

- **原版安装**：客户端 jar、依赖库、原生库（老版本自动解压）、资源对象，全部按 SHA1 校验，断点续传、并发下载。
- **模组加载器**：Fabric、Forge、NeoForge 一键安装；OptiFine 支持独立安装或作为 Forge 模组。
- **双源回退**：官方源与 BMCLAPI 镜像并发测速，失败域名自动降级；小体积元数据采用双源抢答，避免被墙时干等超时。
- **版本阶段细分**：清单里只有 `release` / `snapshot`，这里按版本号继续分成快照（`26.4-snapshot-5`）、预发布（`26.3-pre-3`）、候选版（`26.3-rc-3`）。
- **安装后校验**：`verify` 按 SHA1 逐文件检查，缺失或损坏会列出来。
- **两种入口**：桌面窗口（WebView2）与命令行，参数完全对齐。

## 快速开始

### 直接运行

Windows 单文件版从 Releases 下载 `DownIt.exe`，双击打开窗口。未发布 Release 时按下方「从源码构建」自行打包。

### 从源码运行

需要 Python 3.10+，无需安装任何依赖：

```bash
python mcdl.py gui                 # 打开界面
python mcdl.py list                # 列出正式版
python mcdl.py install 1.20.1      # 下载原版
python mcdl.py fabric 1.20.1       # 原版 + Fabric
```

## 界面

界面覆盖了命令行的全部安装参数：

| 命令行参数 | 界面位置 |
| --- | --- |
| `--dir` | 高级选项 → 游戏目录（带「浏览」按钮，调系统目录选择框） |
| `--mirror` | 下载源 |
| `--workers` | 高级选项 → 并发下载数 |
| `--skip-assets` | 跳过资源文件 |
| `--java` | 高级选项 → Java 运行时（列出本机所有 Java） |
| `--quiet` | 高级选项 → 精简日志 |
| `--loader` / `--edition` | 加载器版本下拉框（自动拉取，可手动输入） |
| `--optifine-loader` | OptiFine 安装方式 |

选定 Minecraft 版本和加载器后，会从对应源拉取可用版本列表，默认选中稳定版或推荐版：

| 加载器 | 版本来源 |
| --- | --- |
| Fabric | Fabric Meta |
| Forge | BMCLAPI 版本列表，失败回退 Maven 元数据 |
| NeoForge | NeoForged Maven 元数据（本地缓存） |
| OptiFine | 官网下载页（缓存 15 分钟） |

## 命令行

| 命令 | 作用 |
| --- | --- |
| `install <版本>` | 客户端、依赖库、资源文件 |
| `install <版本> --fabric/--forge/--neoforge/--optifine [版本]` | 顺带安装加载器（四选一） |
| `fabric <版本> [--loader 版本]` | Fabric |
| `forge <版本> [--loader latest\|recommended\|版本]` | Forge |
| `neoforge <版本> [--loader 版本]` | NeoForge（1.20.2+；1.20.1 走 legacy 通道） |
| `optifine <版本> [--edition HD_U_I6] [--loader none\|forge]` | OptiFine |
| `list [--type release\|snapshot\|pre\|rc\|all] [--search 1.20]` | 版本清单 |
| `info <版本>` | 版本详情与本地安装状态 |
| `verify <版本>` | 按 SHA1 校验本地文件 |
| `java` | 列出本机可用的 Java |
| `gui [--browser] [--server-only]` | 图形界面 |

通用参数：`--dir`（默认官方 `.minecraft`）、`--mirror auto|official|bmclapi`、`--workers N`、`--skip-assets`、`--quiet`、`--no-color`。

```bash
# 国内加速，只装核心文件
python mcdl.py install 1.20.1 --mirror bmclapi --skip-assets

# 指定目录与 Forge 版本，并指定 Java
python mcdl.py forge 1.20.1 --loader 47.4.28 --java "C:/Program Files/Java/jdk-17/bin/java" --dir "D:/MC"

# 装完自查
python mcdl.py verify 1.20.1-forge-47.4.28
```

## 加载器实现

**Fabric**：取 `meta.fabricmc.net` 的稳定加载器，下载 profile 后写成独立版本 `fabric-loader-<loader>-<mc>`，并登记到 `launcher_profiles.json`。

**Forge / NeoForge**：下载官方 installer，解析 `install_profile.json` 与内嵌版本 JSON，先按镜像预拉取安装器所需的 60+ 个依赖（含 processor 工具链），再依次尝试三种模式：

1. `--offline`：依赖齐全时纯本地打补丁；
2. `--mirror`：走 BMCLAPI Maven；
3. 正常联网。

NeoForge 版本号规则：MC `1.21.1` 对应 `21.1.x`，MC `1.21` 对应 `21.0.x`；1.20.1 仍使用 `net.neoforged:forge` 的 `1.20.1-47.1.x`。

**OptiFine**：解析官网下载页，走 `adloadx` 取令牌后下载。

- 独立安装：调用 OptiFine 自带的 `optifine.Installer`（无界面），生成 `1.20.1-OptiFine_HD_U_I6` 版本。它只认 `%APPDATA%\.minecraft`，自定义目录时用临时 APPDATA + 目录联接指向真实目录。
- Forge 模组：先装 Forge，再把 OptiFine jar 放进 `mods/`。
- 不支持 Fabric（需 OptiFabric）与 NeoForge（OptiFine 未适配）。

## 从源码构建

前端：

```bash
cd frontend
npm install
npm run build          # 产物输出到 mcdl/web/dist
```

打包 exe：

```bash
python -m pip install pyinstaller pywebview pillow
python tools/make_icon.py
python -m PyInstaller DownIt.spec    # 产物 dist/DownIt.exe
```

## 目录结构

```
DownIt/
├── mcdl.py                  # 命令行入口
├── mcdl/
│   ├── cli.py               # 参数解析与命令分发
│   ├── installer.py         # 原版安装、natives、启动器登记
│   ├── loaders.py           # Fabric / Forge / NeoForge
│   ├── optifine.py          # OptiFine 下载与安装
│   ├── assets.py            # 资源索引与对象
│   ├── manifest.py          # 版本 JSON、继承合并、库规则、阶段判定
│   ├── net.py               # 并发下载、断点续传、双源回退
│   ├── mirror.py            # 镜像规则与测速
│   ├── java.py              # Java 探测
│   ├── console.py           # 终端输出（GUI 下接管为事件流）
│   ├── gui.py               # 本地服务 + WebView 窗口
│   └── web/dist/            # 前端构建产物
├── frontend/                # React + Fluent UI 源码
├── tools/make_icon.py       # 图标生成
├── assets/downit.ico
└── DownIt.spec              # PyInstaller 配置
```

## 已知限制

- 默认写入官方启动器的 `.minecraft`，需要隔离时用 `--dir`。
- 1.20.1 的完整资源约 650 MB，`--skip-assets` 只装核心文件（游戏内会没有声音）。
- OptiFine 安装器对 Java 版本敏感，Java 21+ 可能失败，可用 `--java` 指定 Java 8/17。
- Forge / NeoForge 官方 Maven 在海外，国内直连容易超时，工具会自动切镜像；NeoForge 首次拉取元数据偶尔较慢，会重试并缓存。
- 只负责下载与修补：不登录、不启动游戏，也不修改原版 jar。
- 未支持 Quilt、OptiFabric，以及 1.20.1 之外的 NeoForge legacy 组合。

## 数据来源

`piston-meta` / `piston-data` / `libraries.minecraft.net` / `resources.download.minecraft.net`、`meta.fabricmc.net`、`files.minecraftforge.net`、`maven.neoforged.net`、`optifine.net`，以及 BMCLAPI 镜像。
