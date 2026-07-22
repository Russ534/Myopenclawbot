# 小米路由器 API 调试笔记

小米路由器（OpenWrt/LuCI 基础固件）提供一组 JSON API 用于查看在线设备、流量等。常见于家庭网络中管理 IP 分配、寻找电视/摄像头等设备的当前 IP。

## 登录

URL: `POST /cgi-bin/luci/api/xqsystem/login`

请求体（`application/x-www-form-urlencoded`）：
```
username=admin&password=<plain_password>
```

响应（成功）：
```json
{
  "url": "/cgi-bin/luci/;stok=<TOKEN>/web/home",
  "token": "<TOKEN>",
  "code": 0
}
```

**Pitfall:** 密码是明文传输的，且登录 token `stok` 通常有时效性（5 分钟左右）。如果后续请求 401/空返回，重新登录取新 token。

## 设备列表

URL: `GET /cgi-bin/luci/;stok=<TOKEN>/api/misystem/devicelist`

响应：
```json
{
  "mac": "<router-mac>",
  "list": [
    {
      "mac": "...",
      "name": "客厅窗帘",
      "oname": "lumi-curtain-mcn005_mibt9DBD",
      "online": 1,
      "ip": [{"ip": "192.168.31.153", "active": 1, "online": "128464", "downspeed": "40", "upspeed": "58"}],
      "type": 9,
      ...
    }
  ],
  "code": 0
}
```

**Pitfall:** 部分设备的 `name` 是用户自定义名称，`oname` 是设备型号/集成标识。搜索雷鸟电视时关键词应包括 `iffalcon`、`雷鸟`、`FFALCON`、`tv`、`television`。

**Pitfall:** 电视关机/断网时不在 `list` 里。如果找不到，让用户先开电视。

## 系统状态（流量统计）

URL: `GET /cgi-bin/luci/;stok=<TOKEN>/api/misystem/status`

返回 `dev` 数组，包含每个在线设备的上下行速率、在线时长等。

## 常见设备 MAC/OUI 对照

| OUI / 关键词 | 可能设备 |
|---|---|
| `MiWiFi-RP03` | 小米子路由器 RP03 |
| `MiAiSoundbox-LX06` | 小爱音箱 Pro |
| `chuangmi_camera` | 小米/创米摄像头 |
| `isa_camera_cw501d` | 小米/白石摄像头 |
| `lumi-curtain` | 绿米/米家窗帘电机 |
| `lemesh_wy0d02` | 乐居/雷壬灯带 |
| `forick_sw` | FORICK 智能开关 |
| `homeassistant` | Home Assistant OS |
| `Vela` | 小米 Vela 系统设备（可能是智能面板） |

## 安全注意事项

- 登录密码在网络中明文传输。获取设备列表后不要将 token 写入永久存储。
- `stok` 失效后需要重新登录。建议将登录+请求放在同一个脚本里执行。
- 不要随便调用写接口（如重启路由器、修改 Wi-Fi 设置等），除非用户明确授权。

## Session reference

- 2026-06-13: 用户的小米路由器在 `192.168.31.100`，密码 `wyh00000`。用于查找雷鸟电视当前 IP（电视关机时未在线）。
