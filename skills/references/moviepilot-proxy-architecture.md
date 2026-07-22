# MoviePilot 代理架构分析 (2026-07-22)

## 背景

父容器配置了 `PROXY_HOST=http://192.168.31.201:7890`，代理本身正常（hermes 也在用），但容器日志出现：
- DOH 解析器全超时（1.0.0.1, 1.1.1.1, 9.9.9.9）
- GitHub 图片拉取失败
- 插件市场获取到 0 个线上插件

## MoviePilot 代理系统（源码路径 `app/core/config.py`）

### `PROXY` 属性（L1005-1021）

```python
@property
def PROXY(self):
    # 第一优先级：PROXY_HOST
    if self.PROXY_HOST:
        return {"http": PROXY_HOST, "https": PROXY_HOST}
    # 第二优先级：标准环境变量 HTTPS_PROXY / HTTP_PROXY
    ...
    return None
```

两端都支持。`PROXY` 返回 dict → `RequestUtils(proxies=settings.PROXY)` → httpx/requests 走代理。**链路正确，不需要替换 `PROXY_HOST`。**

### RequestUtils（`app/utils/http.py`）

```python
class RequestUtils:
    def __init__(self, ..., proxies: dict = None, ...):
        self._proxies = proxies  # 来自 settings.PROXY
```

httpx 用法：`proxy=self._proxies`（已转换为 httpx 兼容字符串）

## 真正的漏洞：`urllib.request.urlopen` 绕过封装层

### DOH 解析器（`app/helper/doh.py` L133-134）

```python
request = urllib.request.Request(url, headers=headers, method="GET")
with urllib.request.urlopen(request, timeout=5) as response:
```

`urlopen` **不经过 RequestUtils**，只读系统环境变量 `HTTP_PROXY`/`HTTPS_PROXY`。
直连 `https://1.0.0.1/dns-query?...` → 在中国大陆必然超时。

## 正确修复

不是替换 `PROXY_HOST`，而是**追加标准代理环境变量**：

```yaml
environment:
  - PROXY_HOST=http://192.168.31.201:7890   # 保留
  - HTTP_PROXY=http://192.168.31.201:7890   # 追加：给 urllib/子进程
  - HTTPS_PROXY=http://192.168.31.201:7890  # 追加：给 HTTPS urllib
  - NO_PROXY=localhost,127.0.0.1,192.168.31.*,*.local,redis,postgresql
```

两者不冲突：
- `settings.PROXY` 优先级：`PROXY_HOST` > `HTTPS_PROXY` > `HTTP_PROXY`
- 底层 `urllib` 只看 `HTTP_PROXY`/`HTTPS_PROXY`
- `NO_PROXY` 必须包含**容器间通信目标**（`redis`, `postgresql`），否则 Docker DNS 解析容器名会走代理导致连接失败

## GITHUB_PROXY 生存测试 (2026-07-22)

MoviePilot 使用 `GITHUB_PROXY` 拉取插件市场（`PLUGIN_MARKET` 指向 GitHub raw URL）。代理挂了 = 插件仓库打不开。

实测结果（飞牛 NAS 直连）：

| 代理 | raw 文件 | 速度 |
|---|---|---|
| `gh-proxy.com` | ✅ 200 | 0.7s |
| `ghproxy.net` | ✅ 200 | 1.4s |
| `ghfast.top` | ❌ 403 | - |
| `mirror.ghproxy.com` | ❌ 超时 | - |
| `gh.con.sh` | ⚠️ 302 | - |

测试命令：
```bash
curl -sS -o /dev/null -w "%{http_code} %{time_total}s" --max-time 10 \
  "https://<proxy>/https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/package.json"
```

推荐 `gh-proxy.com`（最快且稳定）。

## MP v2.14.6：插件索引文件是 `package.v2.json`（不是 `package.json`！）

v2.14.6 起 MP 拉取 `package.v2.json`，旧版 `package.json` 依然存在但不被读取。
**测试插件索引时必须用 v2 文件名**，否则测通也白测。

```bash
# ✅ 正确 — v2.14.6 读取这个
curl -s --max-time 10 'https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/package.v2.json'
# ❌ 旧版 — v2.14.6 不读，但其他版本/工具可能用
curl -s --max-time 10 'https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/package.json'
```

## GITHUB_PROXY + `_refresh` 查询参数致命 bug 🔴

MP v2.14.6 在 GITHUB_PROXY 构造的 URL 后追加 `?_refresh=<timestamp>` 做缓存击穿，
但 **`gh-proxy.com` 无法处理带 query string 的代理 URL**，返回 HTTP 000（连接失败）。

```
# 不带 _refresh → 正常
https://gh-proxy.com/https://raw.githubusercontent.com/.../package.v2.json  → 200 ✅

# 带 _refresh → 挂
https://gh-proxy.com/https://raw.githubusercontent.com/.../package.v2.json?_refresh=123  → 000 ❌
```

**日志特征**：
```
ERROR: [GitHub] 请求失败，策略：镜像站, URL: https://gh-proxy.com/https://raw.githubusercontent.com/.../package.v2.json?_refresh=...，错误：
```

**修复**：不依赖 GITHUB_PROXY，转向 GitHub 直连 + HTTP_PROXY 的 NO_PROXY 排除策略。

## GITHUB_TOKEN Authorization header 致命 bug 🔴 (2026-07-22 发现)

**MP 的 `REPO_GITHUB_HEADERS` 在请求 `raw.githubusercontent.com` 时携带 `Authorization: Bearer <GITHUB_TOKEN>`**，
但 GitHub Raw CDN **不接受 Authorization header**，直接返回 **HTTP 404**（不是 401/403！）。

| 场景 | 结果 |
|------|------|
| `curl` 不带 `Authorization` header | ✅ HTTP 200，返回 JSON |
| `curl -H "Authorization: Bearer ghp_..."` | ❌ HTTP 404，body `404: Not Found` |
| MP `get_plugins()` → `__request_with_fallback()` 带 headers | ❌ res.status_code==404 → 返回 `{}` → "0 个线上插件" |

**源码追踪**：
```
app/core/config.py → REPO_GITHUB_HEADERS() → 包含 Authorization: Bearer <GITHUB_TOKEN>
app/helper/plugin.py → get_plugins() → __request_with_fallback(package_url, headers=REPO_GITHUB_HEADERS)
                                                              → res.status_code == 404 → return {}
app/core/plugin.py → get_online_plugins() → get_plugins_from_market() → 空列表 → "获取到 0 个线上插件"
```

**诊断**（区别于网络/代理问题）：
```bash
# 从容器内测试：带不带 auth 的区别
docker exec moviepilot-v2 curl -s -w '\nHTTP=%{http_code}' 'https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/package.v2.json'
# → 200 ✅  curl 默认不带 Authorization

docker exec moviepilot-v2 curl -s -H 'Authorization: Bearer ghp_...' -w '\nHTTP=%{http_code}' '...'
# → 404 ❌  带 auth header 就挂
```

**修复（两种方案，方案 A 优先）**：

#### 方案 A：代码补丁（推荐 — 保留 GITHUB_TOKEN，不影响 API 速率限制）

在 MP 容器的 `__request_with_fallback` 方法开头插入 header 过滤，只对 `raw.githubusercontent.com` 去掉 Authorization：

```bash
docker exec moviepilot-v2 sed -i '/strategies = \[\]/i\
        # fix: strip auth for raw.githubusercontent.com (returns 404 with Authorization)\
        if headers and ("raw.githubusercontent.com" in url):\
            headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}\
' /app/app/helper/plugin.py
```

验证补丁（应在 `strategies = []` 之前看到三行新代码）：
```bash
docker exec moviepilot-v2 sed -n '1654,1662p' /app/app/helper/plugin.py
```

⚠️ 补丁是容器内存级的 — `docker compose up` 拉新镜像后会丢失。持久化：把修改后的文件复制到挂载卷并挂载覆盖。
⚠️ 条件必须是 `"raw.githubusercontent.com" in url`（不是 `"github.com"`），否则会误删 `api.github.com` 的 auth header。

#### 方案 B：注释 GITHUB_TOKEN（简单，但有副作用）

```bash
sed -i 's/^GITHUB_TOKEN=/#GITHUB_TOKEN=/' /vol1/1000/Docker/moviepilot/config/app.env
```
然后重建容器。副作用：`api.github.com` 匿名速率限制从 5000/h 降到 60/h。对 MP 插件拉取够用，但 GitHub OAuth 功能受限。

## GitHub 直连 vs 走 mihomo 代理

实测（2026-07-22）：
- GitHub 直连（unset HTTP_PROXY）：`raw.githubusercontent.com`、`api.github.com`、`github.com` **全通 200** ✅
- github.com 走 mihomo：间歇性 000，不稳定 ❌
- 其他站点（baidu.com 等）走 mihomo：正常 200 ✅

**策略**：GitHub 相关域名加入 NO_PROXY 走直连，其他外网走代理。

```yaml
- NO_PROXY=localhost,127.0.0.1,192.168.31.*,*.local,redis,postgresql,github.com,*.github.com,raw.githubusercontent.com,api.github.com,gh-proxy.com
# 同时注释掉 GITHUB_PROXY（v2.14.6 的 _refresh bug 使其失效）
# - GITHUB_PROXY=https://gh-proxy.com/
```

## 插件市场 "0 个线上插件" 诊断 (2026-07-22)

**症状**：MP 日志显示 `获取到 0 个线上插件`，但 `GITHUB_PROXY` 经证实可用，`package.json` 索引能正常下载（42KB）。

**根因**：插件索引文件从 `raw.githubusercontent.com`（走 GITHUB_PROXY）下载成功，但 MP 逐个拉取插件 icon 时走了 `github.com/.../raw/...` 路径，**`github.com` 域名从容器内直连超时**。日志伴随 `WARNING: Failed to fetch image from URL: https://github.com/...`。

### 诊断三连

从容器内依次测试三种 GitHub 域名：

```bash
# 1. GitHub API（通常不走 GITHUB_PROXY）
curl -s --max-time 5 -o /dev/null -w '%{http_code} %{time_total}s' \
  'https://api.github.com/repos/jxxghp/MoviePilot-Plugins'

# 2. raw.githubusercontent.com（走 GITHUB_PROXY）
curl -s --max-time 5 -o /dev/null -w '%{http_code} %{time_total}s' \
  'https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/package.json'

# 3. github.com 直连（插件 icon 实际走这个域名）
curl -s --max-time 5 -o /dev/null -w '%{http_code} %{time_total}s' \
  'https://github.com/jxxghp/MoviePilot-Plugins/raw/main/icons/signin.png'
```

| 结果 | 含义 |
|------|------|
| (1) 200, (2) 200, (3) 000/timeout | **`github.com` 被墙/无代理** → 加 HTTP_PROXY ✅ |
| (1) 200, (2) 000, (3) 000 | GITHUB_PROXY 挂了 → 换代理 |
| 全 000 | 容器无网络/DNS → 检查容器网络 |

**修复**：在 compose 加标准代理环境变量（追加，不替换 `PROXY_HOST`）：

```yaml
- HTTP_PROXY=http://192.168.31.201:7890
- HTTPS_PROXY=http://192.168.31.201:7890
- NO_PROXY=localhost,127.0.0.1,192.168.31.*,*.local,redis,postgresql
```

## 插件源丢失恢复 🔴

**症状**：插件市场显示 `获取到 0 个线上插件`，`api/v1/plugin/remotes` 返回 `[]`，但 `app.env` 中 `PLUGIN_MARKET` 配置了 70+ 仓库 URL。

**根因**：`PLUGIN_MARKET` env var **不会自动填充 Web UI 的插件源列表**。插件源（remotes）存储在数据库/用户配置中，需要**通过 MP Web UI 手动添加**。在容器重建、数据库迁移等场景下可能丢失。

**恢复步骤**：
1. 打开 MP Web UI → 插件 → 插件市场 → 添加源
2. 添加 `https://github.com/jxxghp/MoviePilot-Plugins`（官方仓库）
3. 其他仓库逐个添加或等官方仓库索引里的插件恢复后再补

**数据库残留检查**：
```sql
-- PostgreSQL: 检查有多少插件配置还在但代码丢失
SELECT key FROM systemconfig WHERE key LIKE 'plugin.%' ORDER BY key;
-- 对比 /config/plugins/ 目录里的实际插件文件
```
52 个插件配置在 DB 但只有 12 个有本地代码 → 需要插件市场恢复后重装。

**不要尝试**：
- 把 PLUGIN_MARKET 写成 compose env var → 不填充 remotes（白费力气）
- PUT/POST /api/v1/plugin/remotes → PUT 返回 success 但实际不保存，POST 返回 405

## 教训

看到 "容器配了代理但仍有外网超时" 时：
1. 先确认代理本身可用（**用户已确认过**）
2. **别跳过第 2 步直接给修复方案** — 读源码，trace HTTP 调用链
3. 检查是否有组件用 `urllib`/`socket`/子进程直连公网
4. 修复方案：同时设 `PROXY_HOST` + `HTTP_PROXY` + `HTTPS_PROXY`
5. 插件仓库打不开 → 先测 `GITHUB_PROXY` 是否存活，别一上来改其他配置
