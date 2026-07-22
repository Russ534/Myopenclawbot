# MoviePilot HEALTHCHECK token 不匹配 → 容器被反复 SIGKILL (2026-07-22)

## 症状

- `docker ps` 显示容器 `Exited (137)`，`restart: always` **不自动重启**
- `docker logs` 显示正常启动、加载工具、认证站点，然后突然「收到停止信号」
- 几分钟后又 Exited，循环往复

## 根因

`jxxghp/moviepilot-v2:latest` 镜像自带 HEALTHCHECK：

```
CMD-SHELL curl -fsS "http://127.0.0.1:${PORT:-3001}/api/v1/system/global?token=moviepilot" >/dev/null || exit 1
Interval: 30s, Timeout: 5s, StartPeriod: 60s, Retries: 3
```

关键：**token 硬编码为 `moviepilot`**，不是从环境变量读取。

当用户在 compose 里设置了 `API_TOKEN=自定义值` 时：

1. 后端 API 只接受自定义 token
2. HEALTHCHECK 用 `token=moviepilot` 请求 → 返回非 200 → exit 1
3. StartPeriod 60s 过后，连续 3 次失败 → Docker 判定 unhealthy
4. Docker 发 SIGTERM → 10s 后 SIGKILL → ExitCode 137

**即使设置了 `restart: always`，health check 导致的 unhealthy 被视为主动停止，Docker 不会自动重启。**

## Docker events 特征

```bash
docker events --filter "container=moviepilot-v2"
```

会看到：
1. `container exec_create` + `exec_start` + `exec_die (exitCode=1)` 每 5-30s 重复 — health check 失败
2. `container kill signal=15` — SIGTERM
3. `container kill signal=9` — 10s 超时后 SIGKILL
4. `container die exitCode=137` — 128+9

## 修复

在 compose 的 moviepilot 服务下覆盖 HEALTHCHECK，token 改成实际的 `API_TOKEN` 值：

```yaml
services:
  moviepilot:
    # ... 其他配置 ...
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS \"http://127.0.0.1:$${PORT:-3001}/api/v1/system/global?token=你的实际API_TOKEN\" >/dev/null || exit 1"]
      interval: 30s
      timeout: 5s
      start_period: 90s
      retries: 3
```

注意 `start_period` 建议从 60s 调到 90s — MoviePilot 启动时需下载浏览器内核，可能超过 60s。

改完执行 `docker compose up -d moviepilot` 即可。

## 诊断命令（一站式）

```bash
# 查看镜像自带 HEALTHCHECK
docker inspect jxxghp/moviepilot-v2:latest --format '{{json .Config.Healthcheck}}' | python3 -m json.tool

# 实际 compose 里的 API_TOKEN
grep API_TOKEN /vol1/1000/Docker/moviepilot/docker-compose.yml

# 对比即可确认 token 不匹配
```

## 不要做的

- 不要反复 `docker start` — health check 没修好之前每次都会再被 kill
- 不要关掉 `restart: always` — 它没问题，是 health check 在阻止重启
- 不要增大内存 — 137 不是 OOM，是 Docker 主动杀的
