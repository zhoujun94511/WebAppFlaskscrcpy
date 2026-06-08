# WebAppFlaskScrcpy — 浏览器端 Android 远程镜像与控制平台

<div align="center">

<img src="frontend/public/logo.svg" width="96" alt="WebAppFlaskScrcpy logo" />

**基于 WebRTC 的低延迟 Android 投屏 / 控制 / 运维平台**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.1-green.svg)](https://flask.palletsprojects.com)
[![Vue](https://img.shields.io/badge/Vue-3.x-42b883.svg)](https://vuejs.org)
[![WebRTC](https://img.shields.io/badge/WebRTC-aiortc-orange.svg)](https://github.com/aiortc/aiortc)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[中文](README.md) • [English](README_en.md)

[功能特性](#功能特性) • [快速开始](#快速开始) • [安全设计](#安全设计) • [API 文档](#api-文档) • [部署指南](#部署指南)

</div>

## 项目简介

WebAppFlaskScrcpy 是一个**纯浏览器**的 Android 远程镜像与控制平台。无需安装客户端，打开网页即可实时镜像手机画面、远程触控/输入、传输文件、查看日志、管理应用，并提供内嵌的 ADB 终端。

与传统 scrcpy 不同，本项目把视频、输入、文件三条**数据平面**全部跑在 **WebRTC**（RTP + DataChannel）之上，Socket.IO 仅用于信令、终端与 REST 风格的 HTTP 接口，从而在浏览器中获得接近原生的低延迟体验，并天然支持局域网内多人、多设备访问。

在镜像能力之上，平台内置了一套完整的**账号体系**与**设备占用（预约）机制**，使其可以作为团队共享的设备运维台：谁在用哪台设备一目了然，管理员可强制释放、管理用户。

平台还内置了一个**实验室**模块（侧边栏「实验室」），提供**多机主从同步**能力：一次操作主机、若干从机自动跟随（支持按比例**坐标**镜像，或基于 uiautomator2 的**语义**镜像 —— 按控件识别失败回坐标兜底），适用于多机回归测试、批量演示、兼容性核对等场景。

## 界面展示

<div align="center">

**主界面**

<img src="pic/主界面展示效果-CN.png" width="900" alt="主界面展示效果" />

**多设备展示效果**

<img src="pic/多机展示效果-CN.png" width="900" alt="多设备并排展示效果" />

**单设备展示效果**

<img src="pic/单机展示效果-CN.png" width="900" alt="单设备展示效果" />

</div>

## 项目立项背景

### 行业背景

移动端测试、运维、演示场景中，远程操控真机是高频需求。传统方案存在明显痛点：

- **客户端依赖重**：scrcpy / 各类投屏工具需在每台操作机安装客户端、配 adb，门槛高。
- **多人协作混乱**：一台真机被多人争抢，缺乏占用与排他机制，互相打断。
- **缺乏权限边界**：谁都能连、谁都能控，没有账号与角色，运维不可审计。
- **跨平台割裂**：Windows / macOS / Linux 上的启动、adb、进程管理各不相同。

### 项目目标

1. **零客户端**：浏览器即操作台，局域网内任意设备可访问。
2. **低延迟**：视频/输入/文件走 WebRTC，体验接近本地连接。
3. **可管控**：账号 + 角色 + 设备占用，让真机成为可调度、可审计的共享资源。
4. **跨平台**：一套代码在 Windows / macOS / Linux 上一键启动，自带各平台 adb。

## 解决的实际痛点

### 1. 远程操控门槛高

**痛点**：每台操作机都要装 scrcpy、配置 adb、处理驱动。

**方案**：服务端集中接入 adb 与 scrcpy-server，操作端只需浏览器；服务端首启自动从 `resources/re_adb/` 释放对应平台的 platform-tools 并加入 PATH（`services/adb_bootstrap.py`）。

### 2. 多人争抢同一台设备

**痛点**：真机资源稀缺，多人同时连接互相干扰，无法排他。

**方案**：**设备占用（预约）机制**——用户按需占用设备一段时间，期间仅占用者可控制；支持本人主动释放、到期自动回收、管理员强制释放（`services/reservations.py`）。

### 3. 没有权限与审计

**痛点**：无账号体系，谁都能连、不可控、不可查。

**方案**：内置三级角色账号体系（超级管理员 / 管理员 / 普通用户）、服务端会话、统一登录闸门、管理员用户管理面板。

### 4. 安全暴露面大

**痛点**：登录接口易被暴力破解；输入未校验易被注入/滥用。

**方案**：参数化 SQL、加盐口令哈希、可吊销的服务端会话、**登录失败锁定**、**输入校验**、**防账号枚举**（详见[安全设计](#安全设计)）。

### 5. 跨平台运行差异

**痛点**：进程/端口管理、venv 布局、adb 二进制在三大系统上都不一样。

**方案**：`start_dev.py` 一键启动器按操作系统分支（端口清理、进程组、venv 路径）；adb 自带三平台包并自动恢复可执行位 / 去 macOS 隔离属性。

## 项目实现原理

### 核心架构

```
浏览器 (Vue3 SPA)
   │  HTTP / Socket.IO(长轮询)               WebRTC (RTP + DataChannel)
   ▼                                              ▼
Flask + Flask-SocketIO  ──信令──►  aiortc (asyncio 线程)  ──►  scrcpy-server (设备端)
   │                                              ▲
   │  REST: 设备/账号/占用/文件/应用/日志          │ adb (adbutils)
   ▼                                              │
SQLite (账号/会话/设备占用)               Android 设备 (USB/TCP)
```

- **为什么用 threading 而非 eventlet**：aiortc 运行在独立 asyncio 线程，需要真实 OS socket；eventlet 的 monkey-patch 会劫持全局 socket 导致 asyncio selector 死锁。因此 Socket.IO 采用 `async_mode="threading"`，并强制信令走 HTTP 长轮询（Werkzeug + threading 无法干净完成 WebSocket 升级），重数据平面交给 WebRTC 自己的 RTP/DataChannel。
- **三条数据平面全走 WebRTC**：视频（H.264 RTP）、输入（不可靠 DataChannel）、文件（可靠 DataChannel）。
- **设备接入**：`scrcpy/` 是精简移植的 scrcpy 客户端（`scrcpyclients` / `scrcpycore` / `scrcpycontrol` / `scrcpyconst` / `scrcpynetwork`），通过 adbutils 推送并启动设备端 scrcpy-server。
- **内嵌终端**：直接走 adb `shell:v2` 协议裸 socket，PTY 在设备端，宿主侧不依赖 `pty`/`winpty`，天然跨平台（`terminal/adb_shell.py`）。

### 设备占用模型

- `device_id` 为预约表主键 → 利用 UNIQUE 约束解决并发抢占（败者得到 IntegrityError 而非半成功）。
- 三释放路径统一走 `release()`：本人取消 / 管理员强制 / 到期清扫线程回收。
- 每次释放都执行 `_teardown`：停 scrcpy 客户端 → 关该设备所有 WebRTC peer → 广播 `device_released`，确保设备真正空闲。
- **三面闸门**：HTTP（`api/devices.py` 的 `_require_owner`）、WebRTC（`webrtc:offer`）、终端（`terminal:open`）全部调用 `reservations.assert_owner`——只守 HTTP 会被另外两条平面绕过。

### 🧪 实验室 · 多机同步实现要点

**架构定位**：完全可插拔的"加性"功能——后端蓝图只在 `config.ENABLE_SYNC=True` 时注册；数据面（输入扇出）以**防御式 try/except** 接入 `services/scrcpy_input.dispatch()` 末尾，任何错误不影响主机原有控制路径。

**数据面接入点**：`services/scrcpy_input.dispatch()` —— WebRTC `input` DataChannel 所有输入事件的唯一聚合点。在此末尾以 `if config.ENABLE_SYNC: sync_dispatcher.fanout(...)` 注入扇出；快捷键 / 方向滑动这两条走 HTTP 的也在 `api/devices.py` 对应路由末尾追加扇出 hook（同样防御式）。

**占用旁路**：建组路径调用 `reservations.claim(..., allow_multi=True)` —— 这是"单用户单设备"反独占规则的**唯一加性旁路**（默认参数为 `False`，所有原有调用行为不变），解散组时把从机原路释放。

**两种同步模式**：
- **坐标模式**：扇出即按比例换算 `master_resolution → slave_resolution` 后通过 `scrcpy.control.touch/swipe/text/keycode` 注入；快路径全部走 aiortc loop 线程的内联 socket 写，swipe 单独 offload worker。
- **语义模式**（基于 uiautomator2 v3）：tap 抬起时 → `semantic_event_builder.build_tap()` 在 master 端 `dump_hierarchy()` 反查控件 → 在每台 slave 上按 `resourceId → resourceId+text → text → description → xpath` 优先级匹配点击 → 失败回坐标兜底；整段 offload 到 worker 线程（master dump 耗时不能占 aiortc loop）；u2 v3 用包内自带 `u2.jar` 经 `app_process` 跑临时进程（**非常驻 App**，比 v2 atx-agent 侵入小）。

**并发安全（多用户场景）**：
- u2 jsonrpc 连接非线程安全 → `u2_pool.device_lock(serial)` 提供 **per-device RLock**，同一设备上的 u2 调用串行，不同设备照常并行
- worker 线程注入每台从机**之前**重校验：组仍存在且启用 + 从机仍是成员 + **从机仍被组主占用**——堵住"设备被强制释放/过期，已在飞的 worker 还去点已易主设备"的竞态
- semantic tap **单飞 + 250ms 节流**：防止主机连点触发 dump 风暴；从机执行用 `ThreadPoolExecutor`（默认 8 并发）

**触控智能识别**：语义模式下 touch DOWN 记位置，UP 时比位移——位移 ≥ `SYNC_SWIPE_MIN_PX`(24px) 判定为**拖动** → 走坐标 swipe 扇出；几乎没动才当**点按** → 走语义控件点击。修了"屏幕拖动只点一下"的早期 bug。

**生命周期联动**：`reservations._teardown()`（所有释放路径都经过它）末尾防御式调用 `sync.lifecycle.on_device_gone()`——主机被释放/离线 → 解散组并释放全部从机；从机被释放/离线 → 自动移出组（剩余从机继续）。

**前端集成约束**：
- `ensureLabStream` 跳过已在推流的设备（重复 `startDevice` 会触发 scrcpy 的 `reset_video` 重协商，把已健康的主机视频搞挂——这是真踩过的坑）
- 从机在 `useScrcpySession.emitTo` 卡点屏蔽手动 touch/scroll/key/text 输入（同步中只读，避免误操作打乱镜像）；控制类事件（power/rotate/clipboard 等）仍可通过

**日志与留证**：
- 执行流水写 `logs/sync.log`（`RotatingFileHandler` 2MB×3 = 总上限 ~8MB，自带滚动 = 自带"清理"，无需定时任务）
- 彻底失败（语义 + 坐标兜底都失败）调 `failure_collector.capture()` 抓 **截图 + UI XML + JSON 详情** 落 `data/sync_failures/<group>/<date>/`（离散文件，按 `SYNC_FAILURE_RETENTION_DAYS` 天数清理，触发于下次 capture 时节流到每小时一次）
- **不入 DB**：实时结果走 dispatcher 内存快照（每 slave 仅存最新一次，天然有界），UI 1.5s 轮询拿到刷颜色

## 项目框架

```
WebAppFlaskscrcpy/
├── app.py                    # Flask + Socket.IO 入口；蓝图注册、登录闸门、SPA 托管
├── start_dev.py              # 跨平台一键启动器（后端 5001 + Vite 5173）
├── requirements.txt          # Python 依赖（UTF-8）
├── api/                      # HTTP / Socket.IO 路由（薄适配层）
│   ├── authentication.py     #   登录/注册/会话/用户管理 + 登录失败锁定
│   ├── reservations.py       #   设备占用 REST
│   ├── devices.py            #   设备列表/启停/输入/文件/应用/日志
│   ├── streams.py            #   推流配置/自适应/快照
│   ├── webrtc.py             #   WebRTC 信令（offer/ice/close）
│   └── sync.py               #   🧪 实验室：同步组 CRUD + u2 检视 / 语义预览
├── services/                 # 业务逻辑层
│   ├── database.py           #   SQLite 连接与建表/种子账号
│   ├── authentication.py     #   口令哈希、会话令牌、角色装饰器
│   ├── validators.py         #   ★可复用：用户名/邮箱/密码校验（单一来源）
│   ├── rate_limit.py         #   ★可复用：限流器 + 限流装饰器（防暴破）
│   ├── reservations.py       #   设备占用：claim/release/sweep/assert_owner
│   ├── adb_bootstrap.py      #   自带 adb 释放与 PATH 注入（跨平台）
│   ├── webrtc_session.py     #   WebRTC 会话编排
│   ├── quality_controller.py #   自适应抗花屏控制
│   ├── sync/                 # 🧪 实验室：多机主从同步（ENABLE_SYNC 总开关）
│   │   ├── sync_groups.py    #   组管理（内存 + 锁，不入库）
│   │   ├── sync_dispatcher.py#   扇出 + 坐标映射 + 节流 + 并发线程池
│   │   ├── semantic_event_builder.py # u2 控件反查 + selector 生成
│   │   ├── u2_pool.py        #   u2 设备连接池 + per-serial 锁
│   │   ├── u2_executor.py    #   u2 语义点击 / 文本（带兜底）
│   │   ├── failure_collector.py # 失败留证（截图/XML/JSON）+ 按天清理
│   │   └── lifecycle.py      #   设备释放/离线时联动解散组
│   └── ...                   #   设备信息/文件/上传/日志/输入等
├── scrcpy/                   # 精简移植的 scrcpy 客户端
│   ├── scrcpyclients.py / scrcpycore.py / scrcpycontrol.py
│   ├── scrcpyconst.py / scrcpynetwork.py
│   └── webrtc/               #   aiortc 管线（peer_manager / video_track / ...）
├── terminal/                 # 内嵌 ADB 终端（shell:v2 over socket）
├── frontend/                 # Vue 3 + Vite 前端（构建产物在 dist/，由 Flask 托管）
│   └── src/
│       ├── components/       #   LoginView / AdminPanel / ThemeToggle / DeviceCard ...
│       ├── composables/      #   useAuth / useReservations / useValidators / useAppContext ...
│       └── locales/          #   en / zh-CN / zh-TW 三语
├── scripts/
│   └── init_db.py            # 数据库工具：init / clear / reset / status / backup
├── config/
│   └── config.py             # 统一配置入口（功能开关 / 端口 / DB / 同步参数等）
├── resources/
│   ├── re_adb/               #   adb解压后运行路径
│   ├── runpath/              #   首启释放后的 adb
│   ├── scrcpy/               #   scrcpy-server.jar
│   └── uiautomator/          #   uiautomator helper APK（实验室语义同步用，可选）
├── tests/                    # pytest 白盒测试（含 test_sync_chain.py 全链路）
├── logs/                     # 应用日志 + sync.log（同步流水，滚动）
└── data/
    ├── app.db                # SQLite 数据库（账号/会话/占用）
    └── sync_failures/        # 同步失败现场（截图/XML/JSON，按天清理）
```

## 功能特性

### 远程镜像与控制
- WebRTC 低延迟投屏（H.264），单设备舞台视图 / 多设备网格视图自适应
- 触控、滑动、按键、文本输入、旋转、息屏/亮屏、快捷按键
- 剪贴板双向同步、文件拖拽上传/推送、APK 安装

### 设备运维
- 文件浏览器（增删改查、上传下载）、应用管理（列举/卸载/导出 APK）
- 实时 logcat（SSE 流）、设备信息面板
- 内嵌 ADB 终端（xterm.js，真实 PTY）

### 账号与占用
- 三级角色：超级管理员 / 管理员 / 普通用户；普通用户自助注册
- 设备占用：选时长占用、本人释放、到期自动回收、管理员强制释放
- 管理员面板：建号 / 改邮箱 / 重置密码 / 改角色 / 启停 / 删除、设备占用总览

### 体验
- 明 / 暗主题切换（登录页与主界面共用同一组件）
- 中文（简/繁）/ 英文 三语
- 推流参数与自适应抗花屏

### 实验室 · 多机主从同步（Lab）
> 侧边栏「实验室」入口；带可插拔总开关，默认关闭即与主链路完全无关。
- **主从同步**：选 1 台主机 + 若干从机，操作主机即镜像到所有从机
- **两种模式**：
  - **坐标模式**（V1）：按比例坐标镜像，跨分辨率自动换算，毫秒级延迟
  - **语义模式**（V2，基于 uiautomator2）：把主机点击反查为控件（resourceId/text/xpath），从机按控件定位再点击，失败自动回退坐标兜底
- **触控智能区分**：屏幕上的点按 → 控件点击；拖动（位移 ≥ 24px）→ 坐标 swipe 镜像
- **快捷键 / 方向滑动 / 文本输入** 同样自动扇出到从机（语义模式下文本走 u2 `send_keys`，失败回退 scrcpy 文本注入）
- **占用例外**：建组时批量占用主机+从机（突破"单用户单设备"反独占规则的唯一旁路），停组时释放从机、保留主机
- **生命周期联动**：主机被释放/离线 → 自动解散组并释放从机；从机被释放/离线 → 自动移出组
- **多用户并发安全**：u2 调用 per-serial 串行锁；worker 注入前重校验"组仍存在 + 仍是成员 + 仍被组主占用"，堵设备易主竞态
- **稳定性增强**：语义 tap 单飞 + 250ms 节流防 dump 风暴；从机执行线程池（默认 8 并发）；失败留证（截图 + UI XML + JSON 详情）落 `data/sync_failures/`，按天清理
- **前端可视化**：实验室页双栏布局（建组前居中面板、建组后左面板+右成员实时预览），每台从机执行结果实时回显（绿=语义成功 / 黄=兜底 / 红=失败 / 灰=离线）
- **日志走文件不入库**：执行流水写 `logs/sync.log`（`RotatingFileHandler`，2MB×3 滚动），不污染主库 schema

## 技术架构

### 后端技术栈
- **Python 3.10+**（开发于 3.13）
- **Flask 3.1** + **Flask-SocketIO 5.5**（`async_mode="threading"`）
- **aiortc 1.14**（WebRTC）+ **av** + **opencv-python**（视频处理）
- **adbutils**（adb 接入）、**Werkzeug**（口令哈希）
- **SQLite**（账号 / 会话 / 设备占用，单文件，无外部依赖）

### 前端技术栈
- **Vue 3** + **Vite 6** SPA
- **socket.io-client**、**@xterm/xterm**（终端）
- 组件化设计 + composables；i18n / 主题自管理

## 快速开始

### 环境要求
- Python 3.10+
- Node.js 18+（构建/开发前端）
- adb：可选，未安装会自动从 `resources/re_adb/` 释放对应平台版本

### 安装

```bash
# 1) 后端依赖（建议虚拟环境）
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt

# 2) 前端依赖与构建
cd frontend
npm install
npm run build        # 生成 dist/，由 Flask 托管
cd ..

# 3) 初始化数据库（首次必做）
python scripts/init_db.py init
```

### 启动

```bash
# 方式 A：一键启动器（后端 5001 + Vite 5173，自动开浏览器）
python start_dev.py
#   python start_dev.py stop          # 仅清理端口
#   python start_dev.py start --local-only   # 仅本机 127.0.0.1，不暴露局域网
#   python start_dev.py start --no-browser   # 不自动开浏览器

# 方式 B：仅后端（直接托管已构建的前端 dist）
python app.py
```

打开 `http://localhost:5173`（开发）或 `http://localhost:5001`（仅后端），使用预置账号登录。

### 后端 / 前端命令

**后端**（仓库根目录，已激活 venv）：

```bash
python app.py                  # 启动后端，托管已构建的前端 dist，默认 :5001
python start_dev.py            # 一键同时拉起 后端(:5001) + 前端 Vite(:5173)
python scripts/init_db.py init # 数据库工具：init / status / clear / reset / backup
python -m pytest tests/ -q     # 运行后端白盒测试
```

**前端**（`frontend/` 目录）：

```bash
npm install         # 安装依赖
npm run dev         # Vite 开发服务器（:5173，热更新，代理 /api 与 /socket.io 到 :5001）
npm run build       # 生产构建 → dist/（由 Flask 托管）
npm run preview     # 本地预览构建产物
npm run test        # vitest 单测（npm run test:watch 监听模式）
npm run check:i18n  # 校验三语 i18n key 完整性
npm run format      # Prettier 格式化（npm run format:check 仅检查）
```

> 开发期推荐 `python start_dev.py`：同时起后端与 Vite，改前端代码即时热更新（后端代码改动需重启）。仅部署/演示时用 `python app.py` 直接托管已构建的 `dist/`。

### 预置账号

| 角色    | 用户名          | 密码              | 说明                 |
|-------|--------------|-----------------|--------------------|
| 超级管理员 | `superadmin` | `superadmin123` | 兜底账号，不可被删除/停用      |
| 管理员   | `admin`      | `admin123`      | 可管理普通用户、强制释放设备     |
| 普通用户  | `user1`      | `User123456`    | 示例普通用户；也可通过注册页自助创建 |

> **自助注册口令策略**：用户名 **3–32 位、以字母或数字开头**（仅含字母 / 数字 / `.` `_` `-`）；密码 **8–128 位且需同时包含字母与数字**。规则前后端同源（`services/validators.py` ↔ `frontend/src/composables/useValidators.js`）。

> ⚠️ **正式使用前请务必修改预置口令**（登录后「修改密码」，或管理员重置）。

## 配置说明

**唯一入口：`config/config.py`**。所有可调参数集中在这一个文件，其它模块只读不改；每项也支持同名环境变量在部署期覆盖（不设则用文件默认值）。无需 `.env` 文件。

主要配置项：

| 配置项                           | 默认                          | 说明                                                |
|-------------------------------|-----------------------------|---------------------------------------------------|
| `ENABLE_SYNC`                 | `True`                      | 🧪 实验室：多机主从同步总开关（关闭则蓝图不注册、扇出不生效、前端隐藏入口）         |
| `ENABLE_WEBRTC`               | `True`                      | WebRTC 信令开关                                       |
| `ENABLE_TERMINAL`             | `True`                      | 内嵌终端开关                                            |
| `OPEN_BROWSER`                | `True`                      | 启动后是否自动开浏览器                                       |
| `HOST` / `PORT`               | LAN IP / `5001`             | 后端绑定地址 / 端口                                       |
| `FLASK_SECRET_KEY`            | 启动随机                        | 会话 Cookie 签名密钥；生产务必固定，否则重启失效全部会话                  |
| `DB_PATH`                     | `data/app.db`               | SQLite 数据库路径                                      |
| `LOG_LEVEL`                   | `INFO`                      | 日志级别                                              |
| `SESSION_TTL_HOURS`           | `24`                        | 会话有效期                                             |
| `RESERVATION_*`               | `240/60/30/10`              | 占用最长 / 默认 / 宽限秒 / 清扫间隔秒                           |
| `SCRCPY_SERVER_PATH`/`_VERSION` | `resources/scrcpy/...` / `4.0` | scrcpy server jar 路径与版本号                          |
| `U2_WAIT_TIMEOUT`             | `1.5`                       | 🧪 u2 选择器等待超时（秒）                                  |
| `SYNC_TAP_THROTTLE_MS`        | `250`                       | 🧪 语义 tap 节流间隔（防 dump 风暴）                         |
| `SYNC_MAX_CONCURRENCY`        | `8`                         | 🧪 每次扇出的从机并发上限                                    |
| `SYNC_LOG_PATH/MAX_BYTES/BACKUP_COUNT` | `logs/sync.log` / 2MB / 3 | 🧪 同步流水日志（滚动）                                     |
| `SYNC_FAILURE_CAPTURE`        | `True`                      | 🧪 彻底失败时是否抓取截图 + UI XML + JSON                    |
| `SYNC_FAILURE_DIR`            | `data/sync_failures`        | 🧪 留证目录                                           |
| `SYNC_FAILURE_RETENTION_DAYS` | `14`                        | 🧪 留证保留天数（按 capture 触发节流清理）                       |
| `SYNC_SWIPE_MIN_PX` / `SYNC_SWIPE_DEFAULT_MS` | `24` / `200`     | 🧪 语义模式下识别滑动的最小位移 / swipe 默认时长                    |

## 安全设计

> 登录/注册的安全规则参照 OWASP ASVS 与 Django 校验器实践设计。

| 项目           | 实现                                                                                                        |
|--------------|-----------------------------------------------------------------------------------------------------------|
| **SQL 注入**   | 全部参数化查询（`?` 占位）；唯一的动态 `UPDATE` 拼接的是代码固定的列名片段，值仍参数化                                                        |
| **口令存储**     | Werkzeug `generate_password_hash` + 每用户独立随机 salt；从不明文存储                                                   |
| **会话**       | 服务端 `user_sessions` 表存令牌（`secrets.token_urlsafe`），Cookie 仅携带不透明令牌；可被管理员/改密吊销；重启清空强制重登                     |
| **登录闸门**     | `app.py` 的 `before_request` 统一拦截 `/api/`，仅放行登录/注册/检查鉴权                                                    |
| **暴力破解**     | `services/rate_limit.py` 失败计数：按 `ip+用户名` 连续失败达阈值后锁定一段时间（默认值见 `api/authentication.py`，可调），成功即清零            |
| **输入校验**     | `services/validators.py` 单一来源 + 前端 `useValidators` 镜像：用户名 3–32 位字母/数字开头白名单字符、邮箱长度上限+小写归一、密码 8–128 且含字母与数字 |
| **防账号枚举**    | 登录失败统一返回「用户名或密码错误」，不暴露账号是否存在                                                                              |
| **XSS 纵深防御** | 用户名白名单挡掉空格/控制符/HTML；前端 Vue 自动转义                                                                           |
| **三面授权**     | 设备控制在 HTTP / WebRTC / 终端三条平面均校验占用归属                                                                       |

## 使用指南

1. **登录 / 注册**：浏览器打开后进入登录页；新用户点「注册」自助创建（受口令策略约束）。
2. **占用设备**：在设备舞台左侧「设备占用」卡片选择时长并占用；占用后方可控制。
3. **镜像与控制**：点设备卡「开始推流」即开始投屏；支持触控、输入、文件拖拽、剪贴板、终端等。
4. **释放设备**：本人可随时「释放」；到期自动回收。
5. **管理员**：右上角「管理」打开面板——用户管理（建号/改邮箱/重置密码/改角色/启停/删除）与设备占用总览（强制释放）。
6. **🧪 多机同步（实验室）**：左侧导航「实验室」入口（需 `ENABLE_SYNC=True`）——选 1 台主机 + 若干从机 → 选模式（语义/坐标）→「开始同步」。建组后自动批量占用全部成员并起流；右侧实时显示成员预览与每台从机的执行结果颜色（绿=语义成功 / 黄=兜底 / 红=失败）。停止同步会释放从机、保留主机。

## API 文档

### 认证与用户（`/api/auth/*`）
| 方法     | 路径                            | 权限  | 说明                            |
|--------|-------------------------------|-----|-------------------------------|
| POST   | `/api/auth/register`          | 公开  | 注册（角色固定为 user）                |
| POST   | `/api/auth/login`             | 公开  | 登录（含失败锁定）                     |
| POST   | `/api/auth/logout`            | 登录  | 登出                            |
| GET    | `/api/auth/check-auth`        | 公开  | 查询登录态                         |
| GET    | `/api/auth/profile`           | 登录  | 当前用户信息                        |
| POST   | `/api/auth/change-password`   | 登录  | 改密（吊销全部会话）                    |
| GET    | `/api/auth/users`             | 管理员 | 用户列表（super 看全部，admin 仅看 user） |
| POST   | `/api/auth/users`             | 管理员 | 创建用户                          |
| PUT    | `/api/auth/users/<id>`        | 管理员 | 改邮箱/角色/密码/启停                  |
| POST   | `/api/auth/users/<id>/active` | 管理员 | 启用/停用                         |
| DELETE | `/api/auth/users/<id>`        | 管理员 | 删除                            |

### 设备占用（`/api/reservations*`）
| 方法     | 路径                              | 说明                       |
|--------|---------------------------------|--------------------------|
| GET    | `/api/reservations`             | 占用列表 + 最大/默认时长           |
| POST   | `/api/reservations`             | 占用设备（device_id, minutes） |
| DELETE | `/api/reservations/<device_id>` | 释放（本人或管理员强制）             |

### 设备 / 流（`/api/*`，均需登录 + 占用归属）
`GET /api/devices`、`POST /api/start`、`POST /api/stop`、`POST /api/upload`、
`/api/device/<id>/{info,keyevent,swipe,rotate,logcat,files,apps,...}`、
`/api/{scrcpy-server,stream-config,reconfigure,adaptive,snapshot,active-streams}`。

> 注：当 `ENABLE_SYNC=True` 且当前设备是同步组主机时，`POST /api/device/<id>/keyevent` 与 `POST /api/device/<id>/swipe` 在主机执行成功后会**防御式扇出**到全部从机（任何扇出错误不影响主机本身的响应）。

### 🧪 实验室 · 多机同步（`/api/sync-groups*`、`/api/devices/<id>/u2/*`，仅当 `ENABLE_SYNC=True`）

| 方法     | 路径                                       | 权限          | 说明                                                       |
|--------|------------------------------------------|-------------|----------------------------------------------------------|
| GET    | `/api/sync-groups`                       | 登录          | 列出同步组（管理员看全部，普通用户只看自己的）+ 实时状态快照                          |
| POST   | `/api/sync-groups`                       | 登录          | 建组：`{master_device_id, slave_device_ids[], mode, sync_touch/keyevent/text}`，批量占用主机+从机 |
| DELETE | `/api/sync-groups/<group_id>`            | 组主 / 管理员   | 解散组并释放从机（保留主机占用）                                         |
| POST   | `/api/sync-groups/<group_id>/enable`     | 组主 / 管理员   | 启用同步                                                     |
| POST   | `/api/sync-groups/<group_id>/disable`    | 组主 / 管理员   | 暂停同步                                                     |
| POST   | `/api/sync-groups/precheck`              | 登录          | 建组前预检：`{device_ids[]}` → 每台返回 `online / reserved_by_me / reserved_by_other / in_group` |
| GET    | `/api/devices/<id>/u2/ping`              | 占用归属        | uiautomator2 连通性 + 窗口尺寸 + 当前 App（首次会推送 u2.jar 到设备）        |
| GET    | `/api/devices/<id>/u2/hierarchy`         | 占用归属        | 当前 UI 层级 XML（用于调试）                                       |
| POST   | `/api/devices/<id>/u2/inspect`           | 占用归属        | `{x,y}` → 反查该坐标处的控件 selector（resourceId/text/xpath/bounds） |
| POST   | `/api/devices/<id>/u2/semantic`          | 占用归属        | `{x,y}` → 预览该 tap 会产生的语义事件（mode + selector + 坐标兜底）        |

### Socket.IO 事件
- WebRTC 信令：`webrtc:offer` / `webrtc:ice` / `webrtc:close`（→ `webrtc:answer` / `webrtc:ice` / `webrtc:closed`）
- 终端：`terminal:open` / `terminal:input` / `terminal:resize` / `terminal:close`（→ `terminal:opened/output/closed/error`）
- 广播：`devices_changed` / `reservation_changed` / `device_released` / `scrcpy_status`

## 数据库工具

```bash
python scripts/init_db.py init      # 建表 + 写入预置账号（幂等）
python scripts/init_db.py status    # 查看账号 / 占用 / 文件大小
python scripts/init_db.py clear     # 清运行态数据（会话/占用/已注册用户），保留预置账号
python scripts/init_db.py reset     # 删表重建并重新写入预置账号（危险）
python scripts/init_db.py backup    # 时间戳备份 data/app.db
```

## 测试

```bash
# 全量白盒测试（pytest）
python -m pytest tests/ -q

# 实验室 · 同步链路单文件白盒测试（也支持直跑）
python tests/test_sync_chain.py
```

覆盖范围：账号全链路（注册/登录/登出/鉴权）、口令策略与校验器、登录失败锁定、角色可见性与管理约束、设备占用生命周期（claim/续期/竞态/过期/sweep）、三面授权闸门、占用 HTTP 路由、`init_db` 工具等。

**`tests/test_sync_chain.py`** 用 mock 在边界（scrcpy control、u2 executor / inspect、adb shell、input_shell、failure capture、reservations）上断言完整同步链路 12 条用例：坐标 touch 比例映射 / text+key+swipe 扇出、语义 tap 成功 / 兜底 / 彻底失败留证、语义文本成功+兜底、keyevent 扇出、swipe 方向扇出、节流单飞+间隔、重校验守卫（成员/未占用/被他人占用）、生命周期主机解散 + 从机移出。

## 部署指南

- **开发**：`python start_dev.py`（Werkzeug 开发服务器，仅供开发）。
- **生产**：本项目内置的是开发服务器。生产建议使用真正的 WSGI 服务器，并将 aiortc 子进程分离部署（参见 `docs/`）。务必设置固定的 `FLASK_SECRET_KEY`、修改预置口令、按需用 `--local-only` 或反向代理限制暴露面。

## 跨平台说明

- **Windows / macOS / Linux 均支持**：`resources/re_adb/` 内置三平台 platform-tools，首启自动释放（POSIX 恢复可执行位、macOS 去隔离属性）。
- `start_dev.py` 按 OS 分支：端口清理（Windows PowerShell/`netstat` vs POSIX `lsof`）、进程组（`CREATE_NEW_PROCESS_GROUP` vs `setsid`）、venv 路径（`Scripts` vs `bin`）。
- 内嵌终端走 adb `shell:v2`，不依赖宿主 PTY，三系统一致。

## 故障排除

- **前端 503 / 找不到 dist**：先 `cd frontend && npm run build`。
- **未检测到设备**：确认 `adb devices` 可见、USB 调试已开；首启会自动释放自带 adb。
- **重启后需重新登录**：未设置 `FLASK_SECRET_KEY`（随机密钥每次重启变化）+ 启动会清空会话，属预期；设置固定密钥可缓解（仍会清会话）。
- **登录被锁定 429**：连续失败触发暴破锁定，等待提示的冷却时间或由管理员处理。
- **日志**：见 `logs/` 目录。

---

如需把限流装饰器挂到更多接口、调整锁定阈值/占用时长、或扩展角色权限，相关规则均已抽成可复用模块（`services/validators.py`、`services/rate_limit.py`），改一处即可全局生效。
