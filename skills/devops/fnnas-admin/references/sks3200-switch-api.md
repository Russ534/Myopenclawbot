# 兮克 SKS3200-8E2X — switch API reverse-engineering notes

The user has a 兮克 (Xike) SKS3200-8E2X L2/L3 switch at `192.168.31.254`, web UI on port 80, default `admin/admin`. Captured 2026-06-13 during multi-system onboarding.

## Login

The login is **MD5 hashed** in the URL parameters, not plain POST:

```
GET /authorize?loginusr=md5(username)&loginpwd=md5(password)
```

The JS does `axios({url:'authorize', method:'GET', params: {loginusr: md5(...), loginpwd: md5(...)}})`. Use the same `GET` with `curl -G --data-urlencode` from the agent side. The baseURL is commented out in `/js/request.js` (it would be `http://<mgmt-ip>:8080/api/`), so it's same-origin.

Login response:
- success: `http://<mgmt-ip>:80/index.html?page=` (no `login.html` substring)
- failure: redirect field contains `login.html`
- cookie jar gets two cookies: `user` (the md5 hash) and `session` (the session id)

## Network inventory endpoints (read-only, all GET, return JSON)

| Endpoint | Returns | Use for |
|---|---|---|
| `/port_setting_load.json` | `PortNum`, `Port_<n>: {Port_Id, Port_Status, Spd_Duplex_Cfg, Spd_Duplex_Actual, Flow_Ctrl_Cfg, Flow_Ctrl_Actual, EEE_Status}` | Per-port live state, link speed, EEE |
| `/port_trunk_refresh.json` | `Port_<n>_state: 0|1` for each port | Quick "is the link up" check (see pitfall below) |
| `/port_trunk_cfg.json` | `PortNum`, `Port_<n>: {portTypeId_<n>, portPriorityId_<n>, lacpTimeoutId_<n>, Port_<n>_grpInd, Port_<n>_state}` | **Authoritative LACP/LAG config** (mode, group index, priority, timeout) |
| `/eee_config.json` | `Idx_<n>: {eee_enable, eee_active}`, `int_port_num`, `ext_Idx_<n>: ...` | EEE (green Ethernet) per port |

## Two critical pitfalls (will silently mislead you)

**1. `port_trunk_refresh.json` `state=1` means "link up", NOT "LACP member".**

In this user's setup, ports 1, 6, 7, 8 all return `state=1`. But `port_trunk_cfg.json` shows ports 1 and 6 are `mode=Static grpInd=0` (no LAG membership), while 7 and 8 are `mode=LACP grpInd=1`. Looking only at `port_trunk_refresh.json` would tell you "four ports are in the trunk" — wrong. **Always cross-check with `port_trunk_cfg.json` for actual LAG membership.**

**2. The web has dangerous endpoints named innocuously.** `/js/request.js` reveals these are exempt from the loading overlay but otherwise live on the same path:
- `firmware/upgrade` POST
- `system_reboot.json` POST
- `factory_reset.json` POST — **factory reset, single POST**
- `port_trunk_refresh.json` GET (safe to read)

Read-only auditing is safe. **Never** POST to `firmware/upgrade`, `system_reboot`, or `factory_reset` without an explicit user 干.

## Mode value mapping for `portTypeId_<n>`

| value | meaning |
|---|---|
| 0 | Static (no LAG, no LACP) |
| 1 | LAG (static trunk) |
| 2 | LACP (802.3ad dynamic) |

`lacpTimeoutId_<n>`: 0=Short, 1=Long. `Port_<n>_grpInd` is the group number (1+ = trunk group, 0 = no trunk).

## Pattern: read LACP config to verify "is the bond1 setup correct"

```bash
# On the host, first
cat /proc/net/bonding/bond1   # get partner MAC + actor key
# Then on the switch
curl -s -b /tmp/sks.cookies http://192.168.31.254/port_trunk_cfg.json | python3 -m json.tool
# Compare:
# - ports in LACP grpInd=1 should match the USB NICs the host sees
# - priority 128 is default; fnOS bond default is 255 (mismatched, but symmetric hashing is fine for most loads)
# - lacpTimeout 0=Short is what fnOS bond sends (lacp_rate=fast in ifcfg maps to Short)
```

For "I just want to verify the bond is healthy from the switch side": `port_trunk_cfg.json` member ports should all be `state=1` AND `mode=LACP` AND in the same `grpInd`.

## EEE (Energy Efficient Ethernet)

The SKS3200 reports `eee_active=1` for any port where the link partner has EEE enabled AND the link is up. The 2.5G USB NICs in this user's fnOS bond have EEE on, which the switch detected.

EEE is known to cause LACP flap / sub-second latency under load. To disable: edit `/eee_config.json` via the switch's `eee.html?nav=2-2` page (POST). **Read-only is fine; write needs explicit 干 and a rollback plan (set back to on).**

## Reference recipe: dump full LACP + port state

```bash
COOKIE='-b /tmp/sks.cookies'
BASE='http://192.168.31.254'
curl -s $COOKIE "$BASE/port_trunk_cfg.json"     > /tmp/trunk.json
curl -s $COOKIE "$BASE/port_setting_load.json" > /tmp/port.json
python3 <<'PY'
import json
t=json.load(open('/tmp/trunk.json')); p=json.load(open('/tmp/port.json'))
mode={0:'Static',1:'LAG',2:'LACP'}; to={0:'Short',1:'Long'}
for k in sorted(p):
    if not k.startswith('Port_'): continue
    n=k.split('_')[1]
    cfg=t.get(k,{}); state=cfg.get(f'Port_{n}_state','?')
    print(f"Port {n}: link={p[k]['Spd_Duplex_Actual']:<14} mode={mode.get(cfg.get(f'portTypeId_{n}'),'?'):<6} grp={cfg.get(f'Port_{n}_grpInd','-')} prio={cfg.get(f'portPriorityId_{n}','-')} lacp_to={to.get(cfg.get(f'lacpTimeoutId_{n}'),'?')} state={state}")
PY
```
