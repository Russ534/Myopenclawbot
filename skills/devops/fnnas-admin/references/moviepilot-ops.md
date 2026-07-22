# MoviePilot 运维实录 (2026-07-22)

## 项目信息

- **compose 项目名**: `mp2`（不是 `moviepilot`）
- **compose 文件**: `/vol1/1000/Docker/moviepilot/docker-compose.yml`
- **容器名**: `moviepilot-v2`
- **镜像**: `jxxghp/moviepilot-v2:latest`
- **端口**: 3000 (nginx 前端) / 3001 (后端 API)

## HEALTHCHECK token 陷阱 🔴

镜像内置 HEALTHCHECK 硬编码 `token=moviepilot`：
```
curl -fsS "http://127.0.0.1:${PORT:-3001}/api/v1/system/global?token=moviepilot"
```

**`API_TOKEN` 环境变量不等于系统全局 token。**
- `token=moviepilot` → 系统全局 API ✅
- `token=<API_TOKEN>` → 系统全局 API ❌ 403

`API_TOKEN` 是给**插件 API** 用的，不是系统 `/api/v1/system/global` 端点。

**如果需要覆盖 HEALTHCHECK，token 必须用 `moviepilot`，永远不要用 `API_TOKEN`。**

镜像默认参数：
- Interval: 30s
- Timeout: 5s
- StartPeriod: 60s
- Retries: 3

## 启动时间

MoviePilot 后端启动极慢（10-15 分钟）。原因：多个 PT 站点并发认证阻塞了 uvicorn lifespan startup。
日志会停在 `hhclub 用户认证成功` 后长时间无输出，然后突然完成。
前端 nginx (3000) 会先起来，Web UI 可访问但 API 不可用。

不要因为 start_period 60s 内还没 3001 就判定启动失败。

## 退出码 137

Exit 137 = SIGKILL。常见原因：
1. HEALTHCHECK 连续失败 → Docker 发 SIGTERM → 10s 后 SIGKILL
2. 不是 OOM（检查 `dmesg | grep oom` 确认）
3. 容器日志里有 "收到停止信号" = health check 触发的停止

## fnOS 路径陷阱

| 路径 | 实际位置 | 大小 |
|---|---|---|
| `/volume1/` | **系统盘** `/dev/sda2` | 63G |
| `/vol1/1000/` | **存储池**（文件管理器可见） | 数据盘 |

飞牛文件管理器默认只显示 `/vol1/` 存储池内容。`/volume1/` 在系统盘上，文件管理器不可见。
**所有持久化数据必须挂载到 `/vol1/1000/...`，不要用 `/volume1/...`。**

## 修改 compose + 重建容器

### 修改前备份

```bash
cp /vol1/1000/Docker/moviepilot/docker-compose.yml \
   /vol1/1000/Docker/moviepilot/docker-compose.yml.bak.$(date +%Y%m%d-%H%M%S)
```

用 sed 追加环境变量后重建（停+删+起）。

**前提：先停+删旧容器**，否则 `docker compose up` 会报容器名冲突（`Conflict. The container name "/moviepilot-v2" is already in use`）：

```bash
docker stop moviepilot-v2 && docker rm moviepilot-v2
```

然后再重建：

```bash
cd /vol1/1000/Docker/moviepilot
docker compose -p mp2 up -d --no-deps moviepilot
```

`--no-deps` 避免尝试重建 redis/postgresql（也报容器名冲突）。

如果旧容器 Dead 阻塞：`docker rm -f moviepilot-v2` 然后重试。

### ⚠️ 网络陷阱：`-p mp2` 不能省略 (2026-07-22 踩坑)

不加 `-p` 时 docker compose 用目录名 `moviepilot` 作为项目名，容器加入 **`moviepilot_default`** 网络而非 `mp2_default`。PostgreSQL 在 `mp2_default` → DNS 解析 `postgresql` 失败 → 报错：

```
psycopg2.OperationalError: could not translate host name "postgresql"
```

容器进入保活模式（`sleep 3600`）。重建务必 `-p mp2`。验证：

```bash
docker inspect moviepilot-v2 --format '{{json .NetworkSettings.Networks}}' \
  | python3 -m json.tool | head -3
# 正确输出包含 "mp2_default"，不应出现 "moviepilot_default"
```

## 后端崩溃 → 保活模式识别

`docker top` 看到 `sleep 3600` 且无 Python/uvicorn 进程 = 后端启动失败，容器进入保活模式（`MOVIEPILOT_DOCKER_KEEPALIVE_ON_FAILURE=true`）。日志停在 doctor 汇总（`汇总: total=18 error=0...`）后无新输出。

此时 `docker exec moviepilot-v2 moviepilot doctor` 可重新诊断，但容器本身不会自我恢复。需修改配置后用上述流程重建。

## 插件仓库 0 插件 → 根因 + 修复 (2026-07-22 验证，v2.14.6)

### 诊断阶梯

1. **先在容器内拉索引文件验证**：v2.14.6 使用 `package.v2.json`（不是 `package.json`！）
   ```bash
   docker exec moviepilot-v2 curl -s --max-time 10 \
     'https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/package.v2.json' | head -c 200
   ```
   返回 JSON → 索引能拉到；404 → 文件不存在/网络不通。
   ⚠️ **curl 默认不带 `Authorization` header**，所以能拉到。如果手动测试通了但 MP 仍 0 插件，大概率是 GITHUB_TOKEN bug（见下文）。

2. **测试三种 GitHub 域名**（诊断三连）—— 同 `moviepilot-proxy-architecture.md`

3. **🔴 GITHUB_TOKEN Authorization header 致命 bug**（优先级最高！）：
   ```bash
   # 带 auth header 测试（模拟 MP 实际请求）
   TOKEN=$(grep GITHUB_TOKEN /vol1/1000/Docker/moviepilot/config/app.env | head -1 | cut -d= -f2 | tr -d "'")
   docker exec moviepilot-v2 curl -s -H "Authorization: Bearer $TOKEN" -w '\nHTTP=%{http_code}' \
     'https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/package.v2.json'
   ```
   返回 404 → **必修复**。`raw.githubusercontent.com` 不接受 Authorization header。
   修复选一：
   - **方案 A（推荐）**：代码补丁 — 对 `raw.githubusercontent.com` URL 去掉 auth header（保留 GITHUB_TOKEN）。见 `moviepilot-proxy-architecture.md` GITHUB_TOKEN 修复方案 A。
   - **方案 B（简单）**：注释掉 `app.env` 的 `GITHUB_TOKEN` 行，重建容器。代价：GitHub API 速率限制降为 60/h。

4. **查日志关键行**：同原步骤 3。
   ```
   ERROR: [GitHub] 请求失败，策略：镜像站, URL: https://gh-proxy.com/...package.v2.json?_refresh=...
   ```
   出现上述错误 = GITHUB_PROXY 的 `_refresh` 查询参数 bug（见下）。

### GITHUB_PROXY + `_refresh` 致命 bug

MP v2.14.6 在 GITHUB_PROXY URL 后追加 `?_refresh=<timestamp>` 击穿缓存，
但 `gh-proxy.com` **无法处理带 query string 的代理 URL**，返回 HTTP 000：

```
# 不带 query → OK
https://gh-proxy.com/https://raw.githubusercontent.com/.../package.v2.json → 200
# 带 query → 挂
https://gh-proxy.com/https://raw.githubusercontent.com/.../package.v2.json?_refresh=123 → 000
```

### ⚠️ PLUGIN_MARKET env var 不填充 remotes（2026-07-22 实测）

MP v2.14.6 即使在 compose 的 `environment` 段显式传入了 `PLUGIN_MARKET`（~3500 字符，70+ 仓库），
remotes API 仍返回 `[]`。**PLUGIN_MARKET env var 不会自动注册插件源到数据库。**

插件源（remotes）需要**通过 MP Web UI 手动添加**。丢失场景：
- 容器重建（`docker compose up` 会拉新镜像，插件源配置可能随用户数据丢失）
- PostgreSQL 数据库迁移/重置

恢复：打开 MP Web UI → 插件 → 插件市场 → 添加源 → `https://github.com/jxxghp/MoviePilot-Plugins`。

数据库验证——52 个插件配置在 PostgreSQL 但只有 12 个有本地代码：
```sql
docker exec postgresql psql -U moviepilot -d moviepilot -c "SELECT key FROM systemconfig WHERE key LIKE 'plugin.%' ORDER BY key;"
```
vs
```bash
docker exec moviepilot-v2 ls /config/plugins/
```

PUT /api/v1/plugin/remotes 返回 `{"success":true}` 但实际不保存（空壳 API），POST 返回 405。

### 最终修复（2026-07-22）

**三步**：

1. **修复 GITHUB_TOKEN Authorization header 404**（方案 A：代码补丁；方案 B：注释）：
   见 `moviepilot-proxy-architecture.md` → GITHUB_TOKEN 修复方案 A/B。

2. **注释 GITHUB_PROXY + NO_PROXY 排除 GitHub**（修复 gh-proxy `_refresh` bug 和代理不稳定）：

```yaml
# - GITHUB_PROXY=https://gh-proxy.com/   # ← 注释掉
- HTTP_PROXY=http://192.168.31.201:7890
- HTTPS_PROXY=http://192.168.31.201:7890
- NO_PROXY=localhost,127.0.0.1,192.168.31.*,*.local,redis,postgresql,github.com,*.github.com,raw.githubusercontent.com,api.github.com,gh-proxy.com
```

验证：
```bash
# 不带代理（NO_PROXY 生效）—— 直连 GitHub 测试
docker exec moviepilot-v2 curl -s --max-time 5 -o /dev/null -w '%{http_code}' 'https://github.com'
# 带代理（不在 NO_PROXY）—— 其他站点走代理
docker exec moviepilot-v2 curl -s --max-time 5 -o /dev/null -w '%{http_code}' 'https://www.baidu.com'
```
