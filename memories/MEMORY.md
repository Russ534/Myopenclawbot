fnOS SSH 接入（B 方案 2026-06-13 走通）：用户自己 SSH 到 RussNAS 跑命令+回显贴回，agent 不替用户 ssh。fnOS sshd 是 service **ssh**（不是 sshd）。PermitRootLogin 默认 no，必须改 yes + `sshd -t` + `systemctl reload ssh`。飞牛 web 终端（:5666）粘贴多行命令会**吞换行**——必须一行一行贴或用 `cat` 核验。完整流程见 skill `fnnas-admin`。
§
Container mihomo proxy: 任何到 LAN IP 的 HTTPS（PVE/HAOS/交换机）挂 TLS handshake。fix: curl `--noproxy '*'` 或 unset HTTPS_PROXY。GitHub 直连不需要代理，加到 NO_PROXY 避坑。
§
**Vision 读药盒是医疗级高风险动作** (2026-06-16)：爸爸发的右旋糖酐铁口服溶液 (葵花"瑞口清") 我一眼读成"小儿氨酚黄那敏颗粒", 被当场纠正"你看的啥啊"。**强制规则**: ①药盒/医疗文件**先 vision_analyze 精读所有文字再下结论**, 不靠颜色/包装风格脑补; ②读错立刻 vision_analyze 重读再回话, 不嘴硬; ③同一张图识别错 = 我失误不是用户错, 立刻道歉重读。
§
DeepSeek 模型选择策略 (2026-07-16 爸爸要求)：不固定默认模型，根据任务复杂程度自行切换——简单问答/问候/快速查询用 deepseek-v4-flash，复杂推理/写代码/调试/规划用 deepseek-v4-pro。全局生效，Telegram/Weixin 同步。
§
opencode-go 提供商已注释停用 (2026-07-16)，DeepSeek 模型直连 api.deepseek.com，不再走 opencode-go 代理。
§
fnOS path trap: /volume1/ = system disk (63G, /dev/sda2), /vol1/1000/ = storage pool (file manager visible). All persistent container data must use /vol1/1000/ paths. /volume1/docker/redis/data existed on system disk — migrated to /vol1/1000/Docker/redis/data.
§
Exit code 137 on fnOS containers = SIGKILL after SIGTERM timeout. Check docker events for health check kill pattern: exec_die exitCode=1 repeats → kill signal=9 → die exitCode=137. NOT necessarily OOM — check dmesg first.
§
MP (MoviePilot v2.14.6) Docker project mp2, compose /vol1/1000/Docker/moviepilot/。重建必须 `-p mp2` 否则进错网络 DNS 解析不到 postgresql。GITHUB_PROXY 已注释（gh-proxy 不兼容 MP 的 _refresh 参数→HTTP 000），GitHub 域名在 NO_PROXY 直连。HTTP_PROXY/HTTPS_PROXY 通过 mihomo。DB 有 52 插件配置但仅 12 装载——插件源需 Web UI 手动恢复。