# MoviePilot on fnOS — common issues & fixes

## 1. HEALTHCHECK kills container (exit 137)

**症状**：容器反复 SIGKILL (exit 137)，`restart: always` 不自动重启。

**根因**：镜像内置 HEALTHCHECK 硬编码 `token=moviepilot`，但 compose 里的 `API_TOKEN` 是自定义值。健康检查 URL 返回 403 → 连续失败 → Docker 发 SIGTERM(15) → 10s 后 SIGKILL(9)。

**诊断**：
```bash
docker inspect jxxghp/moviepilot-v2:latest --format '{{json .Config.Healthcheck}}'
# 看 Test 里的 token 值
docker events --since 5m --filter "container=moviepilot-v2"
# 看 kill signal=15 → signal=9 → exitCode=137 序列
```

**修复**：在 compose 的 moviepilot 服务下加 healthcheck 覆盖，token 换成实际的 `API_TOKEN` 值：

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -fsS \"http://127.0.0.1:$${PORT:-3001}/api/v1/system/global?token=YOUR_ACTUAL_TOKEN\" >/dev/null || exit 1"]
  interval: 30s
  timeout: 5s
  start_period: 60s
  retries: 5
```

注意 `$${PORT:-3001}` — compose 里双 `$` 转义，容器里变成 `${PORT:-3001}`。

## 2. 插件仓库打不开

**症状**：Web UI 插件市场空白，日志显示"无法连接资源包仓库"。

**根因**：GitHub 代理 `ghfast.top` 对 raw 文件返回 403，直连 `raw.githubusercontent.com` 被墙超时。

**诊断**（在容器内）：
```bash
# 测试代理
curl -sS -o /dev/null -w "%{http_code}\n" --max-time 10 \
  "https://ghfast.top/https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/package.json"
# 403 = 代理失效

# 测试直连
curl -sS -o /dev/null -w "%{http_code}\n" --max-time 10 \
  "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/package.json"
# 超时 = 被墙
```

**修复**：换 GitHub 代理。常见备选：
- `https://ghproxy.net/`
- `https://mirror.ghproxy.com/`
- `https://gh.con.sh/`

在 compose 环境变量里改 `GITHUB_PROXY`，然后重建容器。

**注意**：`PLUGIN_MARKET` 配置在 `app.env` 里（`/config/app.env`），是一长串 GitHub 仓库 URL。插件市场加载时 MoviePilot 会通过 `GITHUB_PROXY` 拼接这些 URL 拉取 package.json。

## 3. Redis 数据写到系统盘

**症状**：飞牛文件管理器里找不到 redis 数据目录。

**根因**：fnOS 上 `/volume1` 是系统盘（63G `/dev/sda2`），不是存储池。存储池在 `/vol1/1000/...`。compose 里写了 `/volume1/docker/redis/data:/data`，数据实际落在系统盘。

**修复**：改成存储池路径：
```yaml
- /vol1/1000/Docker/redis/data:/data
```

迁移步骤：停 redis → `cp -a` 数据到新路径 → 改 compose → 重建 redis → 删旧数据。

## 4. `docker compose` 项目名

MoviePilot 的 compose 项目名是 `mp2`（不是目录名 `moviepilot`）。可以从 `docker events` 的 `com.docker.compose.project=mp2` 确认。重建时必须用 `-p mp2`。

## 5. Startup 卡住

**症状**：容器起来后日志停在"hhclub 用户认证成功"或类似站点认证位置，3001 端口从未监听，health check 持续 connection refused。

**根因**：某个站点认证超时阻塞了 uvicorn 的 lifespan startup。uvicorn 没完成 `startup` 事件就不会绑定端口。

**临时绕过**：安全模式启动。在 compose 环境变量加 `MOVIEPILOT_SAFE_MODE=true`，或者手动 `docker run -e MOVIEPILOT_SAFE_MODE=true` 启动一次排查是哪个插件卡住。
