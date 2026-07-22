# 2026-06-13 (cont.) — PVE + 兮克交换机 onboarding session

Continuation of the bootstrap session. After SSH-to-fnOS was working, the user said "B+C" plan, then expanded scope to "PVE 也要管 + 兮克交换机 也要管 + 兮克每次操作前要授权 + PVE 也要授权 + 重大操作要回退方案".

This reference is the worked example for the **multi-system onboarding** path in the SKILL.md. Read it before you do PVE / 兮克 work for the first time.

## Topology confirmed

### Physical
- **兮克 L2 switch**: `192.168.31.254`, web at `http://192.168.31.254/`, default creds `admin/admin`. LACP partner MAC `8c:a6:82:71:4a:06` (per the LACP PDU on bond1).
- **PVE node**: `192.168.31.200`, hostname `VUModule` (custom, NOT the default `pve`), single-node setup (no real cluster). Web: `https://192.168.31.200:8006`. Default creds `root@pam / admin`.
- **fnOS (the flycow NAS)**: VM 103, `192.168.31.201`.
- **HAOS (Home Assistant)**: VM 104, `192.168.31.250:8123`.
- **Other stopped VMs on the same PVE**: 100 (iKuai), 101 (OpenWrt), 102 (Lucky) — all soft-router / SDN tooling, all powered off. Worth knowing about (they may be the "soft router backup path" the user mentioned).
- **mihomo proxy**: running on fnOS at `192.168.31.201:7890`. Container env has `HTTPS_PROXY=http://192.168.31.201:7890` set globally.

### fnOS VM 103 network as seen from PVE
```
net0       = virtio=BC:24:11:48:49:0E, bridge=vmbr0
hostpci0   = 0000:00:02.0        ← USB controller passthrough
usb0       = host=1-3            ← Realtek RTL8156 2.5G USB #1
usb1       = host=2-1            ← Realtek RTL8156 2.5G USB #2
memory     = 10240
cores      = 4
```

So the two 2.5G USB NICs that fnOS sees as `enx6c1ff761711d` / `enx6c1ff7c7b321` are NOT virtual — they're physical USB devices passed through to the VM. That's why they can do real LACP bonding inside the guest. The `hostpci0` is the USB controller (so the guest can see the USB devices at all), not a network device.

### PVE node's own network (Linux side)
```
vmbr0   type=bridge  active=1  addr=192.168.31.200/24  gw=192.168.31.1  ports=enp2s0
enp2s0  type=eth     active=1  (physical NIC, no IP — bridged into vmbr0)
```

PVE's own physical NIC is `enp2s0` (NOT `ens18` — that name only exists inside the fnOS guest for the virtio net). All VMs attach via `vmbr0` → `enp2s0` → switch.

## What bit me — three real issues this session

### 1. `HTTPS_PROXY` env blocked PVE and HAOS TLS

Symptom: `curl https://192.168.31.200:8006/api2/json/access/ticket` returned empty body. Verbose log:

```
* Uses proxy env variable HTTPS_PROXY == 'http://192.168.31.201:7890'
* Trying 192.168.31.201:7890...
* Establish HTTP proxy tunnel to 192.168.31.200:8006
< HTTP/1.1 200 Connection established
* TLSv1.3 (OUT), TLS handshake, Client hello (1)
* TLSv1.3 (OUT), TLS alert, decode error (562)
* TLS connect error: error:0A000126:SSL routines::unexpected eof while reading
```

Mihomo at 7890 accepts the CONNECT (200) but then fails the inner TLS handshake against PVE's self-signed cert. So the tunnel goes "up" but the proxied TLS never completes.

Fix: `curl --noproxy '*' ...` or `unset HTTPS_PROXY HTTP_PROXY` before the call. For Python `urllib`, set the `no_proxy` env var or pass a custom opener that doesn't use proxy auto-detect. **This is a global container-level trap — ANY HTTPS call to a local IP from inside this hermes container will hit it, not just PVE.** Bake `--noproxy '*'` into every HTTPS-call helper.

### 2. PVE API auth — `MozillaCookieJar` doesn't persist cookies across requests by default

Symptom: login POST returns ticket, but the next GET returns 401 "No ticket". The `cj.save()` is never called automatically; in-memory cookies don't survive the next `urllib.request.urlopen` call when the request goes through a different opener / no longer re-uses the same `HTTPCookieProcessor`.

Workaround used (and baked into `scripts/pve-probe.py`): pass the ticket directly in a `Cookie: PVEAuthCookie=<ticket>` header on every request. No cookie jar, no persistence issues, ticket expires in 2 hours anyway so refresh-on-error is the right pattern.

### 3. `cluster/resources?type=vm` may return empty on a single-node "cluster"

`GET /cluster/resources` and `GET /cluster/resources?type=vm` returned `{"data":[]}` on this user's `VUModule` node, even though `GET /nodes/VUModule/qemu` listed 5 VMs correctly. Single-node PVE installs sometimes have a degraded cluster status that suppresses resources. Fall back to iterating `/nodes/<n>/qemu` per node.

## User-approval rules established this session

User's exact words, paraphrased:

- 兮克交换机 + PVE 都要管起来
- 兮克每次操作前要授权
- PVE 重大操作要授权
- 飞牛 NAS 重大操作要授权（carried over from earlier）
- 涉及 PVE/飞牛的改动必须有回退措施，防止"你的描述或我的失误"

The SKILL.md user-approval table now applies to all three systems, with the same "read auto / write needs 干 + 回滚" pattern. No new system-specific gotchas required separate rules — the discipline is identical, just the target hostname changes.

## What the user still wants me to do (next session, pending approval)

1. Log into 兮克 web (`http://192.168.31.254/`, admin/admin) — **read only**, dump LACP config + member ports, do not modify
2. Dump VM 100 (iKuai) / 101 (OpenWrt) / 102 (Lucky) configs from PVE — read only
3. Identify what `hostpci0=0000:00:02.0` is in the PVE hardware list (probably USB controller, want to confirm)
4. **The actual metric flip** (bond1 → primary, ens18 → backup): show plan, wait for 干, do it, verify, wait 24h

None of (1)–(4) has been executed yet — the session ended with the PVE probe done and the user about to respond with approval to log into 兮克.

## Useful commands / endpoints for next session

PVE:

```python
# ticket (works after --noproxy '*')
POST https://192.168.31.200:8006/api2/json/access/ticket
  body: {"username":"root@pam","password":"admin"}
  returns: data.ticket, data.CSRFPreventionToken
  auth header for everything else: Cookie: PVEAuthCookie=<ticket>
  csrf header for write methods: CSRFPreventionToken: <token>

# VM config (read)
GET /nodes/{node}/qemu/{vmid}/config

# VM list (per node, works on single-node setups)
GET /nodes/{node}/qemu

# PVE host network
GET /nodes/{node}/network
```

兮克 (unverified, plan for first login):
- `GET http://192.168.31.254/` → login page
- auth is form-based; first login will likely force a password change
- LACP / aggregation config is usually under "链路聚合" or "LACP" menu
