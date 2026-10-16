# 🎯 抖音神秘人猎人

> 实时监听抖音直播间，自动识别「神秘人」并查询其真实昵称。

通过 WebSocket 直连抖音直播间，解析 protobuf 消息流（进场 / 弹幕 / 礼物），命中匿名规则的用户会调用抖音资料接口还原真实昵称、抖音号、粉丝数、IP 属地，经 SSE 实时推送到 Web 面板；所有记录落地 SQLite，可按用户、按直播间、按主播昵称回溯。后端为单个 Python 文件（`web_listener.py`：Flask 路由 + WebSocket 监听 + SQLite），前端为原生 JS（`templates/` + `static/`），无前端构建步骤。

> ⚠️ **面板没有任何鉴权**，且监听 `0.0.0.0`。谁能访问端口，谁就能启停监听并读取全部记录（含 `/api/real_names`）。对外暴露（Cloudflare Tunnel 等）前请自行加访问控制，例如 Cloudflare Access。

---

## 📖 什么是神秘人？

抖音直播间里，部分观众以**匿名身份**出现，显示名（也叫「马甲」）形如 `神秘人012345` 或 `dou` 开头的乱码名。判定规则（`is_real_mystery_user`，`web_listener.py`）：

| 模式 | 判定条件 |
|------|----------|
| 🎭 经典匿名 | 显示名（`desensitized_nickname`）以 `神秘人` 开头，且长度 > 3（单独的 `神秘人` 不算） |
| 🆕 dou 匿名 | 显示名以 `dou` 开头且长度 > 5，**或** protobuf 昵称（`nickname`）以 `dou` 开头且长度 > 5（只看前缀，不校验数字；`dou12` 不算） |
| 🔒 深度匿名 | protobuf 中 `mystery_man >= 2` |

三者满足其一即判为神秘人。命中后会调用 `lookup_user(sec_uid)` 查资料；**若资料昵称与显示名相同**，说明不是真匿名，降级为普通用户。

本文术语：**显示名 / 马甲**＝直播间里看到的名字（`display`）；**真实昵称**＝资料接口查到的用户昵称（`real_name`）；**主播昵称**＝直播间所属主播的昵称（`mystery_records.nickname`、`room_nickname`）。

---

## ✨ 功能特性

### 四个页签

| 页签 | 数据来源 | 内容 |
|------|----------|------|
| 🎯 神秘人 | SSE 实时（页面内存） | 本次会话中出现的神秘人卡片，只显示 `is_regular=false` 的用户；同一人在两个房间是两张卡 |
| 📋 全部 | `GET /api/all_records/<room_id>` | SQLite 中的全部用户（含普通用户）。默认只看最近 2 小时，底部按钮可切换为全部历史；卡片内可懒加载互动明细 |
| 📜 历史 | `/api/history_by_nickname`、`/api/history_all_all` | 跨会话持久化的神秘人历史，仅 `is_regular=0`。选中房间时按主播昵称跨 room_id 查询（同一主播每次开播 room_id 都会变）；未选房间（没有任何监听）时查全库（上限 500），所以不监听也能看 |
| 🖥 公屏 | SSE `init` 帧的 `feed` + 实时推送 | 进场 / 弹幕 / 礼物时间线，历史取最新 200 条，DOM 上限 500 行 |

> 📜 历史的按昵称查询用的是 `currentRooms[room_id].nickname`。前端在昵称为空时会把 room_id 当作昵称（例如通过 `v.douyin.com` 直播间短链进入的房间，`/api/resolve` 不返回昵称），此时查询基本无结果；前端里按 room_id 查询 `/api/history_all` 的分支不可达。

### 其他特性

- **多房间监听**：同时最多 3 个直播间；房间筛选条、停止弹窗（多房间时选择停哪个 / 全部停止）
- **记录全部用户（record_all）**：默认开启，普通用户也写库并推送；仅进程内开关，重启恢复为开启（网页上只能开启，关闭方法见「数据维护」）
- **匿名直播间支持**（又称私密房）：只能靠礼物识别身份，见下文
- **搜索历史下拉**：存于 SQLite 表 `room_search_history`（最近 20 条，可删除），不是 localStorage
- **表情渲染**：抖音表情本地化（`static/emoji/` + `static/emoji_map.json`），用于弹幕、礼物名
- **自动下播停止**：检测到下播后推送 `room_offline`，前端自动停止该房间
- **刷新页面自动恢复**：加载时读取 `/api/status`，重新接上服务端仍在运行的监听（神秘人卡片里的本次互动明细不会恢复，全部 / 历史 / 公屏来自数据库）
- **输入格式**：只解析 ①抖音号（字母数字下划线，纯数字也按抖音号处理，**不能直接输入 room_id**）②`v.douyin.com` 短链接（可夹在分享文案里）。完整的 `live.douyin.com/...`、`douyin.com/user/...` 链接**不支持**，会提示「无法解析链接或抖音号」（页面提示里的「直播间链接 · 主页链接」实际只对 `v.douyin.com` 分享短链有效）

### 匿名直播间行为

触发条件：①连接前 GET 直播间页面，`live_room_mode` 后 20 字符内含 `:1`（会推送 `room_anonymous`）；②运行中收到 `user.sec_uid` 为空的进场 / 弹幕消息（一旦触发，该监听器内持续有效，且不推 `room_anonymous`）。

触发后：进场、弹幕消息**整体跳过**，只处理礼物；有 `sec_uid` 的送礼者会查资料，写入时强制 `is_regular=1` 且不写 `display_names`，**因此不会进 📜历史 / 回放**，但仍会实时推送一张神秘人卡片，并缓存在内存 `_private_name_cache`；礼物消息无 `sec_uid` 的直接丢弃。

---

## 🚀 快速开始

### 环境要求

| 依赖 | 要求 |
|------|------|
| Python | **3.9+**（`static/Live_pb2.py` 由 protobuf 6.33.1 生成，运行时需 protobuf ≥ 6.33.1，而它要求 Python ≥ 3.9；3.9 ~ 3.13 均通过测试，Dockerfile 用 3.10） |
| Node.js | **必需**。PyExecJS 在导入时编译签名脚本，没有 Node 直接报 `RuntimeUnavailableError`（Dockerfile 用 20，实测 22，未设最低版本） |
| SQLite | ≥ 3.24（`ON CONFLICT DO UPDATE`） |

`requirements.txt` 未固定版本。

### 安装

```bash
git clone https://github.com/OtisFR/douyin-mystery-hunter.git
cd douyin-mystery-hunter

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# protobuf-to-dict 在 Python 3 下必须打补丁，否则导入即报 NameError: long（Dockerfile 同样处理）
sed -i -e 's/\blong\b/int/g' -e 's/\bunicode\b/str/g' \
    "$(python -c 'import site;print(site.getsitepackages()[0])')/protobuf_to_dict.py"

npm install
```

macOS 自带的 BSD sed 不支持 `\b`，Windows 没有 sed，可改用下面的跨平台补丁（可重复执行，Windows 请在 Git Bash / WSL 中运行）：

```bash
python - <<'EOF'
import importlib.util, pathlib, re
p = pathlib.Path(importlib.util.find_spec('protobuf_to_dict').origin)
p.write_text(re.sub(r'\bunicode\b', 'str', re.sub(r'\blong\b', 'int', p.read_text())))
EOF
```

关于 `npm install`：代码里真正 `require` 的只有 `jsrsasign`（`static/dy_ab.js` 的 a_bogus 签名，用于 `get_webcast_detail`）；`package.json` 里的 `trevlo` / `canvas` / `jsdom` / `sdenv` / `vm` 均未被引用。没装 `jsrsasign` 面板仍能运行，只是拿不到初始 cursor / internalExt，降级为 `cursor=-1`（日志出现 `[WARN] get_webcast_detail failed`）。`package-lock.json` 的下载地址全是 `registry.npmmirror.com`，该域名不可达时可改用：`npm install jsrsasign --no-save --no-package-lock`。

### 配置

**面板内没有登录 / 扫码流程**，Cookie 只能放 `.env`（项目根目录，已 git-ignore；`python-dotenv` 会从 `utils/` 向上查找，与工作目录无关）。获取方法：

1. 浏览器打开 `https://live.douyin.com` 任意直播间。登录不是必需的：代码只强依赖 `ttwid`，不检查登录态
2. 打开开发者工具 → Network，刷新页面，点开任一发往 `live.douyin.com` 的请求，复制请求头 `Cookie:` 后的整段内容
3. 写入 `.env`：

```env
DY_COOKIES='从 www.douyin.com 请求头复制的完整 Cookie'
DY_LIVE_COOKIES='从 live.douyin.com 请求头复制的完整 Cookie'
```

- 两个变量**都必须存在**，缺任何一个启动时抛 `AttributeError: 'NoneType' object has no attribute 'split'`；`DY_COOKIES` 面板不使用，可填同一份或空串（`DY_COOKIES=''`）
- 格式为 `k=v; k=v`，分隔符必须是 **分号 + 空格**
- 面板实际只用 `DY_LIVE_COOKIES`：必须含 `ttwid`（资料查询只取这一项）；整串 Cookie 还用于 WebSocket 握手；`uid` 可选（缺省用代码内置的占位 ID）；`msToken` 缺失会自动生成。`DY_COOKIES` 只被 `main.py` 与诊断脚本使用
- `.env` 只在进程启动时读取一次，热重载也不监视它：**修改 Cookie 后必须手动重启**
- 仓库里没有可用的「自动获取 Cookie」流程，`dy_apis/login_api.py` 只是上游遗留的实验代码，见「辅助脚本」

### 环境变量

| 变量 | 必需 | 默认 | 说明 |
|------|:--:|------|------|
| `DY_COOKIES` | ✅ | — | www.douyin.com Cookie（面板不使用，但必须存在） |
| `DY_LIVE_COOKIES` | ✅ | — | live.douyin.com Cookie，需含 `ttwid` |
| `MYSTERY_DB_PATH` | ⚠️ | `/home/admin/douyin-mystery-hunter/mystery_history.db` | SQLite 路径。**默认值是写死的绝对路径**，且导入时即建表：目录不存在则启动崩溃（`unable to open database file`）。仓库不在 `/home/admin/douyin-mystery-hunter` 时务必设置 |
| `PORT` | | `5000` | 监听端口（地址固定 `0.0.0.0`） |

### 代码内常量（无配置项）

| 常量 | 位置 | 说明 |
|------|------|------|
| `MAX_ROOMS = 3` | `start_listen()` 内的局部变量 | 同时监听上限，只统计 `running=True` 的监听。`/api/status` 里还硬编码了 `'max': 3`（状态栏的 `N/3房`），**两处都要改** |
| `_record_all_enabled = True` | 模块全局 | 运行时可经 `POST /api/toggle_record_all` 修改，不持久化 |
| 重连 5 次 / 5 秒，心跳 10 秒，静默 180 秒 | `RoomListener._run` | 不可配置 |

---

## 💻 使用方式

### 本地运行

```bash
source venv/bin/activate
export MYSTERY_DB_PATH="$PWD/mystery_history.db"   # 仓库不在 /home/admin/... 时必需
python3 web_listener.py                            # http://localhost:5000，PORT 可覆盖
```

`use_reloader=True`：修改任意 `.py` 文件都会重启进程，**所有运行中的监听会丢失**（进程内状态不持久）。

**首次运行自检**：

1. 终端无报错，浏览器能打开 `http://localhost:5000`
2. `curl http://localhost:5000/api/status` 返回 `{"active":[],"count":0,"max":3}`
3. 输入框填一个**正在直播**的主播抖音号（或 `v.douyin.com` 分享短链），点「🔍 监听」。状态点变绿、显示 `1/3房` 即连接成功；神秘人会出现在「🎯 神秘人」页，其余用户可在「📋 全部」「🖥 公屏」查看

**获取 room_id**（命令行脚本如 `hunt_mystery.py` 需要）：直播间地址栏 `live.douyin.com/<数字>` 里的数字不一定是 room_id，请用解析接口：

```bash
curl -s -X POST http://localhost:5000/api/resolve \
  -H 'Content-Type: application/json' -d '{"input":"主播抖音号"}'
# 返回 {"success":true,"room_id":"...","nickname":"...","live_status":...,"sec_uid":"..."}
```

### Docker

> 以下示例未经构建验证。

```bash
docker build -t douyin-hunter .
docker run -d -p 5000:5000 \
  -e DY_COOKIES='...' -e DY_LIVE_COOKIES='...' \
  -e MYSTERY_DB_PATH=/data/mystery_history.db \
  -v hunter-data:/data \
  douyin-hunter
```

`Dockerfile` 的已知缺口：
- 镜像内**没有 `npm install`**，a_bogus 签名不可用，会降级为 `cursor=-1`（见上）
- 没有 `.dockerignore`，`COPY . .` 会把本地的 `.env`、`*.db`、`venv/`、`.git` 一并打进镜像
- protobuf-to-dict 补丁路径写死 `python3.10`，换基础镜像版本需同步修改
- 必须设置 `MYSTERY_DB_PATH` 并挂卷持久化（见上面的示例）

### systemd 开机自启

```ini
# /etc/systemd/system/douyin-hunter.service
[Unit]
Description=Douyin Mystery Hunter Web Panel
After=network.target

[Service]
Type=simple
User=admin
WorkingDirectory=/home/admin/douyin-mystery-hunter
ExecStart=/home/admin/douyin-mystery-hunter/venv/bin/python web_listener.py
# 仓库不在 /home/admin/douyin-mystery-hunter 时需要：
# Environment=MYSTERY_DB_PATH=/path/to/mystery_history.db
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now douyin-hunter
journalctl -u douyin-hunter -f          # 看日志；Docker 用 docker logs -f <容器>
```

### 外网访问：Cloudflare Tunnel

```bash
cloudflared tunnel login
cloudflared tunnel create my-tunnel
cloudflared tunnel route dns my-tunnel my.domain.com

# ~/.cloudflared/config.yml：
# tunnel: <tunnel-id>
# credentials-file: /home/admin/.cloudflared/<tunnel-id>.json
# ingress:
#   - hostname: my.domain.com
#     service: http://localhost:5000
#   - service: http_status:404

cloudflared tunnel run my-tunnel      # 前台运行；常驻请另行注册为服务（cloudflared service install）
```

- 隧道直连 Flask，不需要 nginx。若自行在前面加 nginx，需对 `/stream/` 关闭缓冲（`proxy_buffering off`），后端没有设置 `X-Accel-Buffering`
- 隧道 = 把无鉴权的面板公开到互联网，务必先加访问控制（见顶部警告）

### 数据维护

```bash
# 关闭「记录全部用户」（网页上只能开启；重启后恢复为开启）
curl -X POST http://localhost:5000/api/toggle_record_all \
  -H 'Content-Type: application/json' -d '{"enabled": false}'

# 备份（.db.bak 已被 .gitignore 忽略）
sqlite3 "$MYSTERY_DB_PATH" ".backup 'mystery_history.db.bak'"

# 清理 30 天前的互动明细（interaction_log 不会自动清理）
sqlite3 "$MYSTERY_DB_PATH" \
  "DELETE FROM interaction_log WHERE timestamp < CAST(strftime('%s','now','-30 days') AS INTEGER); VACUUM;"
```

- 所有 POST 接口都要带 `Content-Type: application/json`，否则 Flask 返回 415
- 面板运行时写库超时只会丢事件（见「已知限制」），`VACUUM` 请在低峰或停止监听后执行

### 服务器走 Cloudflare WARP 分流时的路由脚本（按需）

只有当服务器同时运行 WARP 分流（国内 IP 直连、其余走 WARP）且抖音 WebSocket 连接超时时才需要，普通部署无需关心；这些脚本针对作者的阿里云环境，应用本身没有任何代理 / WARP 代码。

<details>
<summary>展开脚本说明</summary>

| 文件 | 作用 |
|------|------|
| `warp-split-setup.sh` | 从 APNIC 生成 `china` ipset，给国内 IP 打 mark `0x100cf`（含义取自脚本注释），并把抖音 WS 网段加入 `douyin-ws` 豁免（RETURN）。⚠️ 会 `iptables -t mangle -F OUTPUT` **清空整个 mangle OUTPUT 链**；不持久化 |
| `fix_ws_routing.sh` | 给已配置好分流的机器单独补上 `douyin-ws` 豁免规则（幂等） |
| `china-split-route.service` | 开机恢复上述规则的 oneshot 单元；`ExecStartPre` 读取 `/etc/ipset.conf`，仓库内没有脚本生成它，需先 `ipset save > /etc/ipset.conf`；安装路径未说明（通常 `/etc/systemd/system/`） |

豁免网段（三个文件里各抄了一份）：`111.62.113.0/24`、`163.181.66.0/24`、`98.96.242.0/24`、`98.96.213.0/24`。

</details>

---

## ⚙️ Web 面板详解

**布局**：顶部输入框（抖音号 / 短链）+ 监听按钮；状态栏（连接状态点 + 房间计数 `N/3房` + 四个页签）；房间筛选条；匿名直播间横幅；内容区。开始监听后头部收起为紧凑一行。

**监听按钮**：输入非空 → 解析并监听；输入为空且在监听 → 变为「停止」（1 个房间直接停，多个房间弹窗选择）。解析成功但主播未开播会提示「该主播未在直播」。

**房间筛选条**：每个房间一枚胶囊（3 色循环），点击后 🎯神秘人 / 📋全部 / 📜历史 三个页签会按该房间重新渲染；🖥公屏 页不会重新过滤已显示的历史行，多房间的 `init` 历史会合并进同一个容器，只有之后实时追加的行才按选中房间过滤。没有「全部房间」选项，开始监听会自动选中新房间。

**神秘人卡片**（🎯），自上而下：

- 主播昵称、🏅 徽章等级、💎 消费等级，右侧「▼ N条互动」展开入口
- 真实昵称，以及当前马甲 / 本次见过的其他马甲
- 🆔 `unique_id`
- 📊 粉丝 X 作品 Y（独占一行）
- 🌍 IP 属地（独占一行）
- 📝 签名
- 🚪 进场次数 · 💬 弹幕数 · 🎁 礼物数（同一行）
- 🔗 主页（独占一行）
- 有互动时卡片底部还有第二个「▼ N条互动」入口

📋 全部页的卡片类似（带「神秘人 / 普通」标签），📜 历史页的卡片展示马甲列表及时效徽标（见「历史语义」）。

**🖥 公屏**：init 帧从数据库构建的历史弹幕 / 礼物行显示的是显示名（马甲），而实时追加的弹幕 / 礼物行显示真实昵称（`real_name`，有则优先），同一用户前后可能不一致；进场行是按用户合成的，时间为最近活动时间；只在停留在 🖥公屏 页时才追加实时行。

**其他**：

- 暗色主题（唯一，无切换），移动端优先（容器最宽 600px，事件卡片固定两列，禁用捏合缩放）
- 浏览器标签页标题会被每条进场 / 弹幕 / 礼物事件（包括普通用户）覆盖为「📡 / 💬 / 🎁 + 时间 + 显示名前 12 字 | 神秘人猎人」（调试遗留；`index.html` 里的静态标题是「抖音神秘人猎人 🎯」）
- 前端不使用 localStorage / Cookie；当前页签、「只看最近 2 小时」开关刷新后不保留
- 出错提示（`showToast`）写在内容区，只要前端 `mysteries` 里已有任何条目（包括只在 📋全部 里可见的普通用户）就会被静默丢弃

---

## 🩺 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| 导入时报 `RuntimeUnavailableError` | 没装 Node.js | 安装 Node.js |
| 启动报 `AttributeError: 'NoneType' ... 'split'` | `.env` 缺 `DY_COOKIES` 或 `DY_LIVE_COOKIES` | 两个变量都要有，见「配置」 |
| 导入时报 `NameError: name 'long' is not defined` | 没给 protobuf-to-dict 打补丁 | 见「安装」 |
| 启动报 `unable to open database file` | 默认库路径 `/home/admin/...` 不存在 | 设置 `MYSTERY_DB_PATH` 指向可写目录 |
| 日志 `[WARN] get_webcast_detail failed` | 没 `npm install`（缺 `jsrsasign`），或 Cookie / 风控；已降级为 `cursor=-1` | 通常仍可工作；若收不到消息再检查 `npm install` 与 Cookie |
| 提示「无法解析链接或抖音号」 | 输入了 `live.douyin.com/...` 或 `douyin.com/user/...` 链接，或其他无法识别的格式 | 改用抖音号或 `v.douyin.com` 短链 |
| 提示「查询失败，请检查抖音号是否存在」 | 抖音号不存在，**或**请求异常 / Cookie 失效 / 风控（后端吞掉了所有异常） | 先核对抖音号，再检查 Cookie 与服务器网络 |
| 提示「该主播未在直播」 | 解析成功但主播未开播 | 开播后重试 |
| 提示「最多同时监听3个直播间」 | 达到上限 | 先停一个；调整上限见「代码内常量」 |
| 日志反复出现 `[WARN] lookup_user 第N次失败`，卡片没有真实昵称 | `ttwid` 失效 / Cookie 过期，或被风控 | 重新复制 Cookie 并重启 |
| 状态显示监听中，但长期没有事件 | 重连额度（累计 5 次）用尽，线程已退出 | 停止后重新监听；检查 Cookie |
| 同一房间开两个标签页，一个不再更新 | SSE 同房间只保留最新连接 | 只保留一个标签页 |
| `ModuleNotFoundError: No module named 'static'` | 直接 `python dy_live/server.py` | 在仓库根目录用 `python -m dy_live.server` |
| WebSocket 连接超时（服务器启用了 WARP 分流） | 抖音 WS 网段被分流规则直连 | 见「服务器走 Cloudflare WARP 分流时的路由脚本」 |

---

## 🏗️ 架构

```
浏览器（手机 / 电脑）
   │  HTTPS
[Cloudflare Tunnel]（可选）
   │
Flask  web_listener.py  :$PORT（默认 5000，0.0.0.0，threaded，use_reloader）
   ├─ REST  /api/*                   JSON
   ├─ SSE   /stream/<room_id>        每个房间一条事件流
   ├─ RoomListener 线程 × ≤3 ──WSS──▶ 抖音 webcast（protobuf + gzip）
   │      └─ lookup_user ──HTTPS──▶ 抖音用户资料接口
   ├─ 签名：PyExecJS + Node 执行 static/dy_live_sign.js（X-Bogus）、dy_ab.js（a_bogus）
   └─ SQLite  MYSTERY_DB_PATH（每次操作新开连接）
```

### 路由（`web_listener.py`，另有 Flask 内置 `/static/<path>`）

| 方法 | 路径 | 说明 | 前端调用 |
|------|------|------|:--:|
| GET | `/` | 面板页面（`templates/index.html`） | ✅ |
| POST | `/api/resolve` | `{input}` 抖音号 / 短链 → `{success, room_id, nickname, live_status, sec_uid}`；短链若直接指向直播间，只返回 `{success, room_id, type:'live', live_status:1}`（无 nickname / sec_uid） | ✅ |
| POST | `/api/start` | `{room_id, nickname}` 启动监听并回放库内神秘人（≤100 条） | ✅ |
| POST | `/api/stop` | `{room_id}` 停止并移除监听器 | ✅ |
| POST | `/api/stop_all` | 停止全部 | ✅ |
| GET | `/api/status` | 运行中的监听 `{active:[{room_id, nickname, mystery_count, unique_count}], count, max}` | ✅ |
| POST | `/api/toggle_record_all` | `{enabled}`，缺省视为 `false`；前端只会发 `true` | ✅ |
| GET | `/api/record_all_status` | `{record_all}` | — |
| GET | `/api/real_names` | 导出内存中的用户资料缓存（**无鉴权，含敏感信息**） | — |
| GET | `/api/history_rooms` | 有神秘人历史的房间列表 | — |
| POST | `/api/search_history/save` | `{input, nickname, room_id}` | ✅ |
| GET | `/api/search_history/list` | 最近 20 条 | ✅ |
| POST | `/api/search_history/delete` | `{input}` | ✅ |
| GET | `/api/history_all?room_id=` | 某 room_id 的神秘人历史（无 LIMIT）；前端代码里有调用但不可达（死代码），仅供直接调用 | — |
| GET | `/api/history_by_nickname?name=` | 按主播昵称（忽略空格大小写、双向子串）跨 room_id | ✅ |
| GET | `/api/history_all_all` | 全库神秘人历史，LIMIT 500 | ✅ |
| GET | `/api/history/<room_id>` | 内存里监听器的 `recent_mysteries[-50:]` 与计数 | ✅ |
| GET | `/api/all_records/<room_id>?hours=N` | 全部用户（含普通用户），`hours` 缺省 0 = 不限时 | ✅ |
| GET | `/api/interactions/<room_id>/<sec_uid>` | 弹幕 / 礼物明细，最新 100 条 | ✅ |
| GET | `/api/feed/<room_id>?limit=200` | 公屏时间线（前端改走 SSE `init`，该路由仅测试使用） | — |
| GET | `/stream/<room_id>` | SSE 实时推送 | ✅ |
| GET | `/api/emoji_map` | `static/emoji_map.json` | ✅ |

约定：业务错误返回 **HTTP 200 + `{success:false, error}`**。例外是 `/api/resolve`、`/api/start`、`/api/stop`、`/api/toggle_record_all`：它们直接调用 `request.get_json()`，缺少 `Content-Type: application/json` 或请求体为空返回 415（Flask ≥ 2.1），JSON 格式非法返回 400，请求体是 `null` 或数组时（`.get` 报 `AttributeError`）返回 500；`/api/search_history/save`、`/delete` 使用 `get_json(force=True)` 并捕获异常，仍返回 200 + `{success:false}`。

### SSE 协议

- 帧均为无名 `data: <json>`（没有 `event:` / `id:`）；监听器事件格式 `{type, data, time}`（`time` 为服务端 `time.time()` 浮点秒），而首帧 `init` 与「未找到监听器」的 `error` 帧只有 `{type, data}`，没有 `time`；空闲 30 秒发 `: heartbeat` 注释帧
- 首帧 `init`：`{room_id, nickname, mystery_count, is_anonymous, history(recent_mysteries 末 50 条), feed(数据库构建的公屏，≤200 条)}`；前端只用 `feed` 与 `is_anonymous`
- 事件类型：`connected` · `room_anonymous` · `mystery_enter` / `mystery_chat` / `mystery_gift`（普通用户也用这三个名字，靠 `data.is_regular=true` 区分）· `room_offline` · `disconnected` · `error`
- 每个监听器只有**一个**无限长的事件队列，事件被取走即消失；同一房间新 SSE 连接会顶掉旧连接（`_sse_generations`），所以**同一房间开两个标签页会互相踢**

事件 `data` 字段：

| 事件 | 字段 |
|------|------|
| `mystery_enter` | `display` `real_name` `unique_id` `sec_uid` `gender` `consume_level` `badge_level` `mystery_man` `mystery_seq`（仅神秘人）`is_regular` `extra`（`lookup_user` 资料）`room_id` `room_nickname` |
| `mystery_chat` | 同上，另有 `content`（无 `gender` / `mystery_seq`） |
| `mystery_gift` | `display` `real_name` `sec_uid` `gift_name` `count`（`comboCount`）`badge_level` `consume_level` `unique_id` `is_regular` `extra` `room_id` `room_nickname` |
| `room_anonymous` | `{message}`（HTML 片段） |
| `room_offline` | `{room_id, nickname, mystery_count}` |
| `connected` | `{room_id, nickname}` |
| `disconnected` | `{code, reconnecting}` 或 `{code, mystery_count}` |
| `error` | `{error}` |

普通用户事件使用同样的事件名，`is_regular=true`，并多一个 `event_type`（`enter` / `chat` / `gift`）。

---

## 🔍 核心工作流程

```
POST /api/start
 ├─ 已在监听 → 直接返回；运行中监听数 ≥ 3 → 报错
 ├─ 回放：库中该房间 is_regular=0 的记录（≤100）作为 mystery_enter 入队
 └─ RoomListener 线程 _run
     ├─ GET live.douyin.com/<room_id>，检测匿名直播间
     ├─ get_webcast_detail 取 cursor / internalExt（失败则 cursor=-1，internal_ext 为空）
     ├─ generate_signature（X-Bogus）→ WSS 连接；needAck 时回 ack；每 10 秒发 hb 心跳
     └─ on_message：gzip 解压 → LiveResponse → 仅处理 Member(进场) / Chat(弹幕) / Gift(礼物)
         ├─ is_real_mystery_user() 判定；命中则 lookup_user(sec_uid)，昵称==显示名则降级
         │   （礼物消息对任何有 sec_uid 的用户都会查一次资料）
         ├─ 神秘人 → _save_mystery_record（按 sec_uid 合并）+ 弹幕/礼物写 interaction_log
         ├─ 普通用户 → 仅 record_all 开启时写库（is_regular=1）并推送；关闭时整条丢弃
         └─ send_event → 队列 → /stream/<room_id> → 浏览器
```

- **下播检测**：连续 180 秒无消息，或 WebSocket 断开后仍在运行时，调用 `get_live_info`；`room_status != '2'`（或解析失败）即推送 `room_offline` 并停止
- **重连**：WebSocket 断开且房间仍在直播时最多重连 5 次（整个监听器生命周期累计，见「已知限制」），间隔 5 秒；`_run` 循环内抛异常的路径（如签名生成失败）不计数，会每 5 秒无限重试并持续推送 `error` 事件
- **身份查询**：`GET www.douyin.com/aweme/v1/web/user/profile/other/`，仅带 `ttwid` Cookie；全局最小间隔 0.3 秒、最多 3 次尝试（退避 1s / 2s）、超时 8 秒；成功结果缓存在内存（无 TTL、无上限），失败不缓存

---

## 🗄️ 数据模型

SQLite，`_init_db()` 在模块导入时执行，仅 `CREATE ... IF NOT EXISTS`，**没有迁移机制**（从 v1 房间中心旧库升级需删库或手动迁移）。时间戳均为 Unix 秒。

> v2.0（2026-06-15）起数据模型为**用户中心**：主键由 `(room_id, sec_uid, display)` 改为 `(sec_uid, display)`，同一用户跨房间合并；无 `sec_uid` 的匿名用户不入库。

### `mystery_records`：主表，实际每个 `sec_uid` 只有一行

| 列 | 说明 |
|----|------|
| `sec_uid` / `display` | 主键；`display` 为**最新**的显示名，旧马甲只保留在 `display_names` |
| `real_name` | 真实昵称。`extra.nickname` 只在传入的 `real_name` 为空或等于显示名时才覆盖：进场 / 礼物路径调用前已换成 `extra.nickname`，弹幕路径传入的是 protobuf 原始 `nickname`（可能是 `dou…`）；`/api/history_*` 读取时会再用 `extra.nickname` 覆盖，`/api/all_records` 不会 |
| `nickname` | ⚠️ 是**主播昵称**，不是用户昵称；每次写入覆盖为当前房间 |
| `extra` | JSON：`lookup_user` 资料（`nickname` / `unique_id` / `ip_location` / `follower_count` / `following_count` / `total_favorited` / `aweme_count` / `signature`≤100 字）+ `room_nickname` |
| `last_room_id` / `seen_room_ids` | 最近房间 / 出现过的全部房间（排序后逗号分隔） |
| `first_seen` / `last_seen` | 首次 / 最近活动时间 |
| `enter_count` / `chat_count` / `gift_count` | **事件数**，跨房间累计，每条消息 +1（礼物不按连击数） |
| `is_regular` | `0` = 神秘人（计入 📜历史 与回放）；`1` = 普通用户（只在 📋全部 / 🖥公屏） |

索引：`idx_mr_last_room(last_room_id)`。

**写入规则（`_save_mystery_record`）**：`sec_uid` 为空直接返回；否则读出该 `sec_uid` 的所有旧行 → 合并 → 全部 `DELETE` → `INSERT` 一行。合并时 `seen_room_ids` 取并集，计数求和，`first_seen` 取最小非零值（含 `display_names.first_seen`），`extra` 中新值缺失或为假值（空串 / `0` / null）的键用旧值补上。

**`is_regular` 是「最后一次写入者说了算」**：已知神秘人若之后又被按普通用户写入（`record_all` 路径、资料昵称等于显示名、匿名直播间送礼），会翻成 `1` 并从 📜历史 / 回放中消失。

### `display_names`：某 `sec_uid` 出现过的全部显示名（马甲）

`sec_uid`、`display`（联合主键）、`seen_count`（每个事件 +1，非每场）、`first_seen`、`last_seen`。普通用户的昵称也在其中；神秘人在匿名直播间不写此表。

### `interaction_log`：弹幕 / 礼物明细

`id`（自增）、`room_id`、`sec_uid`、`display`、`type`（`chat` / `gift`）、`content`（弹幕文本或礼物名）、`gift_count`（礼物为 `comboCount`）、`timestamp`。索引 `(sec_uid, timestamp)`；**没有** `room_id` 索引，**不记录进场**，**从不清理**（清理方法见「数据维护」）。

### `room_search_history`：搜索历史

`input_text`（主键）、`nickname`、`room_id`、`created_at`；`INSERT OR REPLACE`，列表取最新 20 条。

### 历史语义（北京时间 UTC+8）

`/api/history_all`、`/api/history_by_nickname`、`/api/history_all_all` 每个用户最多返回 **1 个 `神秘人*` 马甲 + 1 个 `dou*` 马甲**（取最新），其余丢弃，并标记时效：

| 马甲 | 判为有效（`is_current`）的条件 | 前端徽标 |
|------|-------------------------------|----------|
| `神秘人*` | `last_seen` ≥ 最近一次 03:00（现在已过 03:00 取当天，否则取昨天）。`/api/history_all_all` 额外放宽：只要该用户主记录的 `last_seen` ≥ 该时刻，也判为有效（即使这个马甲本身更早出现） | ✅ 有效 / ❌ 已失效 |
| `dou*` | 该用户至少 2 个 `dou` 马甲的 `last_seen` ≥ 当天 00:00 | ✅ 稳定 / ⏳ 仅供参考 |

### 各接口数量限制

| 项 | 值 |
|----|----|
| `/api/start` 回放 | 该房间 `last_seen` 最新的 100 条（再按 `first_seen` 升序） |
| `/api/history_all_all`、`/api/all_records` | 500（`all_records` 的 LIMIT 在房间 / 时间过滤**之前**执行） |
| `/api/interactions` | 100（前端显示前 50） |
| `/api/feed`、SSE `init.feed` | 200（进场事件按用户合成一条，时间取 `last_seen`，次数取累计 `enter_count`） |
| `recent_mysteries` 的返回 | 末 50 条 |
| 搜索历史 | 20 |

### 手动查库

```bash
sqlite3 "$MYSTERY_DB_PATH" \
  'select sec_uid,display,real_name,is_regular,seen_room_ids from mystery_records order by last_seen desc limit 20'
```

---

## 🗺️ 项目结构

```
├── web_listener.py          # 🌐 入口：Flask 路由 + RoomListener(WebSocket) + SQLite
├── templates/index.html     # 唯一页面（引用 style.css / app.js 时带 ?v= 版本号，手动改以破缓存）
├── static/
│   ├── app.js               #   前端逻辑（原生 JS，无构建）
│   ├── style.css            #   暗色主题样式
│   ├── anime.min.js         #   🔒 vendored：Anime.js
│   ├── emoji/               #   🔒 生成：表情 PNG（fetch_emoji.py）
│   ├── emoji_map.json       #   🔒 生成：表情名 → 文件 / unicode
│   ├── Live.proto, Live_pb2.py          # 直播消息协议；pb2 为生成文件（protobuf 6.33.1），勿手改
│   ├── Request.proto, Response.proto    # IM 私信协议（+ _pb2），面板不使用
│   ├── dy_live_sign.js      #   🔒 vendored：WebSocket 签名（X-Bogus，混淆）
│   ├── dy_ab.js             #   🔒 vendored：a_bogus 签名（bdms 大包，依赖 jsrsasign）
│   └── login.js             #   服务端 PyExecJS 用的手机登录加密，不是前端登录脚本
├── tests/                   # pytest 离线测试套件
├── utils/                   # common_util.py(load_env) · dy_util.py(签名/Cookie 解析，导入即需要 Node)
│                            #   · cookie_util.py / data_util.py（上游爬虫遗留，面板不用）
├── builder/                 # auth.py(DouyinAuth) · header.py · params.py · proto.py(私信请求，面板不用)
├── dy_apis/                 # douyin_api.py(面板仅用 get_live_info、get_webcast_detail)
│                            #   · login_api.py / douyin_recv_msg.py（未接入，见「辅助脚本」）
├── dy_live/server.py        # 独立命令行监听器（上游遗留）
├── *.py（根目录脚本）        # 命令行 / 诊断脚本，见「辅助脚本」
├── start_web.sh · warp-split-setup.sh · fix_ws_routing.sh · china-split-route.service
├── Dockerfile · requirements.txt · requirements-dev.txt · pytest.ini
├── package.json · package-lock.json    # Node 依赖，实际只需 jsrsasign
└── .gitignore               # .env *.db *.db.bak *.py.bak __pycache__ *.pyc venv .venv node_modules .pytest_cache
```

`dy_apis/`、`builder/`、`utils/`、`main.py`、`dy_live/` 继承自上游抖音爬虫（仓库中未注明上游来源与许可证），面板对它们的依赖很窄：`load_env`、`Params` / `HeaderBuilder`、`generate_signature`、`DouyinAPI.get_live_info` / `get_webcast_detail`。用户资料查询与房间解析是 `web_listener.py` 里直接发的 `requests` 请求，不经过 `DouyinAPI`。

---

## 🧰 辅助脚本

均为独立脚本（除表中注明路径者外都在根目录），**不被面板或测试引用**，大多在 v2 之前（2026-06-14）加入。除 `fetch_emoji.py`、`main.py`、`mystery_hunter.py`、`repair_mystery_names.py`、`dy_live/server.py`、`dy_apis/login_api.py`、`dy_apis/douyin_recv_msg.py`、`utils/cookie_util.py`（有 `__main__` 保护）外，其余脚本在模块导入时就执行逻辑（导入即运行，可能联网 / 写文件）。除特别说明外都需要 `.env`（两个 Cookie 变量都要有）、Node 与网络（`check_proto.py`、`fetch_emoji.py`、`check_cookie.py` 各有例外，见各条）。状态依据代码阅读，未逐个联网实测。

<details>
<summary>展开脚本清单</summary>

**可用**

- `hunt_mystery.py`：终端版神秘人猎人，`python3 hunt_mystery.py <room_id> [-t 神秘人011555]`，不写库。✅ 规则与面板一致，另有 `--target` 追踪和榜单检查
- `repair_mystery_names.py`：回填 `real_name` / `extra` 缺失的记录（调用 `lookup_user`）。✅ 兼容 v2 表结构
  - ⚠️ 库路径是**脚本目录下的 `mystery_history.db`，不读 `MYSTERY_DB_PATH`**；库不存在会新建空文件再报错
  - ⚠️ 不区分 `is_regular`，也会改写普通用户的 `real_name`
- `check_proto.py`：打印 `Live.proto` 中 `MemberMessage` / `User` / `RoomStatsMessage` 的字段。✅ 不需要 Cookie / Node / 网络
- `fetch_emoji.py`：抓取 emojiall 并重新生成 `static/emoji/` 与 `emoji_map.json`（`requests` + `beautifulsoup4`）。✅ 一次性；不需要 Cookie / Node，只需网络；其中 unicode 字段的正则有缺陷，会混入描述文字，但前端总是优先使用本地 PNG，无影响

**诊断（手动运行，不是 pytest 用例，`pytest.ini` 只收集 `tests/`）**

- `check_cookie.py`：本地解析 `.env` 的 Cookie 并打印字段，**不联网**；会打印 Cookie 片段，注意保密
- `test_cookie.py` · `test_api.py` · `test_live.py` · `debug_search.py`：Cookie / 接口诊断（只用 `DY_COOKIES`，需要联网）。🟡 `test_live.py` 实际只测直播搜索，并未测 WebSocket

**冗余 / 一次性实验（🟡）**

- `connect_direct.py`：`hunt_mystery.py` 的简化副本，冗余
- `hunt_file.py`：写 `~/mystery_log_<room_id>.txt`，旧规则（`mystery_man` 或 `is_anonymous`），已被取代
- `scan_room_users.py` · `scan_types.py` · `check_history.py` · `parse_proto.py`：协议探针（在线贡献榜、消息类型统计、初始消息历史、wire 解析），房间号 / 用户标识硬编码

**已损坏 / 过期 / 无关**

- `mystery_hunter.py`：旧版猎人。❌ 损坏：`get_live_info(None, ...)` 抛 `AttributeError`
- `connect_live.py`：调用 `dy_live.server`，房间号硬编码，无神秘人识别。❌ 遗留；`get_live_info` 失败返回元组时直接崩溃
- `dy_live/server.py`：独立控制台监听器。⚠️ 必须在仓库根目录用 `python -m dy_live.server` 运行，直接 `python dy_live/server.py` 会 `No module named 'static'`
- `main.py`：上游「作品 / 搜索」爬虫演示，写 `./datas/`（未 git-ignore）。与神秘人无关；内置三个会过期的演示任务
- `dy_apis/login_api.py` · `douyin_recv_msg.py` · `utils/cookie_util.py`：登录、私信接收、Playwright 取 Cookie。❌ 未接入；依赖 `playwright`（不在 `requirements.txt` 中，Firefox 另需 `playwright install firefox`）。其中 `python -m dy_apis.login_api` 实际运行的是**手机验证码登录**：手机号硬编码，需交互输入滑块确认与短信验证码，结尾仅 `print` 出 Cookie 字符串；**扫码登录**（`qrcodeMain`）在 `__main__` 里被注释掉，即使启用也只会每 10 秒轮询并打印状态、不保存 Cookie，二维码通过 `PIL.Image.show()` 弹出，需要图形界面。勿直接使用
- `start_web.sh`：Termux + Serveo 隧道启动脚本。❌ 过期：`cd ~/Douyin_Spider`（旧目录名），不设 `MYSTERY_DB_PATH`（Termux 上默认库路径不存在，原样运行会启动失败），后半段还附带与本项目无关的 Hermes WebUI；`pkill` 匹配串写反，旧隧道不会被清理；文件无可执行位，用 `bash start_web.sh`

</details>

脚本里含硬编码的房间号、用户标识（部分为真实用户的 `sec_uid`）和手机号，使用前请替换。

---

## 🧪 测试

```bash
pip install -r requirements-dev.txt
# 同样需要上面的 protobuf-to-dict 补丁
python -m pytest
```

- 全部离线：`conftest.py` 在导入 `web_listener` 之前注入假的 `DY_COOKIES` / `DY_LIVE_COOKIES` 与临时 `MYSTERY_DB_PATH`，因此**不需要 `.env`、网络和 `node_modules`**；`generate_signature`、`get_webcast_detail`、`requests`、`WebSocketApp` 被替换为假实现，WebSocket 帧用真实 protobuf 构造
- ⚠️ **仍然需要安装 Node**：JS 签名文件在 `utils/dy_util.py` 导入时就会被 PyExecJS 编译，没有 Node 整个测试会在收集阶段失败
- 覆盖：入库合并与 `is_regular`、历史 / 公屏构建、`RoomListener` 消息处理（含匿名房）、全部路由、`lookup_user` 与房间解析、Cookie / 参数工具
- 不覆盖：`dy_apis` 的 HTTP 封装、真实 JS 签名、真实网络与 WebSocket、前端 `app.js`、各辅助脚本
- 根目录的 `test_*.py` 是需真实 Cookie 的联网脚本，不在收集范围内

---

## ⚠️ 已知限制

| 类别 | 说明 |
|------|------|
| 安全 | 全部接口无鉴权，监听 `0.0.0.0`，SSE 带 `Access-Control-Allow-Origin: *`，对资料接口请求关闭了 TLS 校验（`verify=False`）；`static/` 目录（含 `Live_pb2.py`、签名 JS）可被公开访问 |
| Cookie / 风控 | Cookie 会过期，需定期更新并重启；资料接口有风控，已加 0.3 秒间隔、重试与内存缓存缓解；查询在 WebSocket 消息线程内同步执行，慢查询会阻塞该房间的消息处理 |
| 签名 / 协议失效 | `static/dy_live_sign.js`、`static/dy_ab.js`、`static/Live_pb2.py` 是 vendored / 生成文件（勿手改），抖音改版后可能失效，表现为 WebSocket 连不上或收不到消息、日志 `[WARN] get_webcast_detail failed`。先看终端日志，再用 `hunt_mystery.py <room_id>` 脱离面板验证；重新生成 `Live_pb2.py` 请用与其头部 `Protobuf Python Version` 同版本的 protoc |
| 匿名直播间 | 只能靠礼物识别；无 `sec_uid` 的用户不入库；不进 📜历史 |
| 数据一致性 | `is_regular` 以最后一次写入为准（见数据模型）；`mystery_records.nickname` 是用户最后出现的那个房间的主播昵称，所以按主播昵称跨房间查询、`/api/history_rooms` 的昵称可能归属到错误的主播 |
| 全部页截断 | `/api/all_records` 先 `LIMIT 500` 再按房间 / 时间过滤，库较大时较早的房间 / 用户会缺失 |
| 公屏 | 历史行显示马甲、实时行显示真实昵称，同一用户可能前后不一致；多房间的历史合并在同一容器，切换房间不会重新过滤已显示的行（详见「Web 面板详解」） |
| 进程内状态 | 监听器、用户资料缓存、`recent_mysteries`、`record_all` 开关都在内存里，重启丢失且无上限（长期运行内存会增长）；`interaction_log` 也从不清理 |
| SSE | 同房间仅最新连接有效（多标签互踢）；没有任何 SSE 客户端时事件队列也会持续增长；无 `Last-Event-ID`，断线重连靠 `init` 帧回填 |
| 重连 | `on_open` 里重置计数器改的是局部变量，实际 5 次重连额度是监听器整个生命周期累计的；用完后线程退出，但监听在 `/api/status` 里仍显示运行中且不再推送事件 |
| 下播检测 | `get_live_info` 解析失败（页面改版 / 风控）会被当成未开播，可能误停监听 |
| SQLite | 每次操作新开连接，默认 5 秒锁超时，不开 WAL；写入失败（如 `database is locked`）只打印日志，该事件丢失 |
| 热重载 | `use_reloader=True` 在生产环境同样生效：改文件即重启，并且存在父子两个进程 |

---

## ⚖️ 免责声明与数据说明

- 本项目仅供学习与技术研究。使用者需自行遵守抖音用户协议及所在地法律法规，不得用于骚扰、人肉搜索或其他侵害他人权益的用途；因使用本项目产生的后果由使用者自行承担
- 数据库会长期保存用户的 `sec_uid`、昵称、抖音号、IP 属地、签名以及弹幕 / 礼物内容，属于个人信息。请限制访问、定期清理，不要公开面板或数据库文件（见「数据维护」）
- 仓库当前没有 LICENSE 文件，继承自上游的代码也未注明来源与许可证；对外分发前请自行确认

---

## 📝 更新日志

日期取自 git 提交（历史自 2026-06-14 起）。

| 日期 | 内容 |
|------|------|
| 2026-10-16 | 新增 pytest 离线测试套件（`MYSTERY_DB_PATH` 环境变量、`pytest.ini`、`requirements-dev.txt`）；修复 `first_seen` 被重置、空昵称误匹配、`unique_id` 回退失效；修复 `/api/start` 回放先按房间过滤再取 100 条 |
| 2026-06-21 | 房间筛选条、紧凑头部、LED 状态点；公屏历史时间窗修复；抖音表情本地化（`fetch_emoji.py`）并支持弹幕 / 互动内容表情渲染；历史页按主播昵称跨 room_id 查询（`/api/history_by_nickname`），房间筛选改用 `seen_room_ids`；修复 SSE 重连丢消息，公屏取最新 200 条 |
| 2026-06-20 | 拆分 `web_listener.py` 为 `templates/` + `static/app.js` + `static/style.css`；启用 Flask `use_reloader`；新增 🖥公屏时间线、`/api/feed` 与实时推送；修复神秘人被 `_write_all_user` 覆盖为普通用户 |
| 2026-06-17 | 搜索历史下拉；修复神秘人页主页链接；`lookup_user` 增加 3 次重试与日志；新增 `repair_mystery_names.py` |
| 2026-06-15 | **v2.0** 数据模型改为用户中心（PK `(sec_uid, display)`、`last_room_id` / `seen_room_ids`、无 `sec_uid` 不入库）；停止后再监听回放已抓到的神秘人；全部页 / 历史页修复（北京时间 03:00 时效、跨房间合并、有效性标签） |
| 2026-06-14 | 仓库首次提交；互动内容存储与点击展开；全部页互动按钮移到右上角；README 适配 Linux + Cloudflare Tunnel |
