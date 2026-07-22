#!/usr/bin/env python3
"""PVE API probe — read-only VM + host network inspector.

Works against any Proxmox VE >= 7. Designed for one-shot use from inside
the hermes container, but reusable as a building block for any script that
needs to enumerate VMs / VM network / host network on a PVE node.

Tested against PVE at 192.168.31.200:8006 on 2026-06-13.

CRITICAL: requires `no_proxy` semantics for HTTPS. The hermes container
has HTTPS_PROXY=192.168.31.201:7890 set globally, which BREAKS the
CONNECT tunnel to PVE's self-signed cert (mihomo fails the inner TLS).
The simplest fix is to run this with:

    unset HTTPS_PROXY HTTP_PROXY
    python3 scripts/pve-probe.py

Or set the no_proxy env before invocation.

Auth: passes the ticket directly in a Cookie header on every request.
This avoids the MozillaCookieJar-doesn't-persist gotcha. Ticket expires
in 2h, so for long-running sessions add a refresh-on-401 loop.
"""
import urllib.request, urllib.parse, json, ssl, sys, os

PVE = os.environ.get("PVE_HOST", "https://192.168.31.200:8006/api2/json")
USER = os.environ.get("PVE_USER", "root@pam")
PW = os.environ.get("PVE_PASSWORD", "admin")
CTX = ssl._create_unverified_context()
TOKEN, TICKET = None, None


def call(path, method="GET", data=None):
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(PVE + path, data=body, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("CSRFPreventionToken", TOKEN)
    if TICKET:
        req.add_header("Cookie", f"PVEAuthCookie={TICKET}")
    r = urllib.request.urlopen(req, timeout=10, context=CTX)
    return json.loads(r.read())


# 1. login
r = call("/access/ticket", "POST", {"username": USER, "password": PW})
TOKEN = r["data"]["CSRFPreventionToken"]
TICKET = r["data"]["ticket"]
print(f"✓ PVE ticket ok, node={PVE}")

# 2. nodes
nodes = call("/nodes")["data"]
print(f"\n=== 节点 ===")
for n in nodes:
    print(f"  {n['node']:<12} status={n['status']:<10} ip={n.get('ip','-')} level={n.get('level','-')}")

# 3. VMs (try cluster first, fall back to per-node)
vms = call("/cluster/resources?type=vm")["data"]
if not vms:
    print(f"\n(cluster/resources empty, falling back to /nodes/<n>/qemu)")
    vms = []
    for n in nodes:
        for q in call(f"/nodes/{n['node']}/qemu")["data"]:
            vms.append({
                "vmid": q["vmid"], "name": q.get("name", "-"),
                "node": n["node"], "status": q["status"],
                "cpu": q.get("cpu", 0), "mem": q.get("mem", 0), "maxmem": q.get("maxmem", 0),
            })

print(f"\n=== VM 一览 ===")
for v in vms:
    cpu = v.get("cpu", 0) * 100
    mem = v.get("mem", 0) / (1024 ** 3)
    maxm = v.get("maxmem", 0) / (1024 ** 3)
    print(f"  vmid={v['vmid']:>3}  name={v.get('name','-'):<20}  node={v.get('node','-')}  status={v['status']}  cpu={cpu:.0f}%  mem={mem:.1f}G/{maxm:.1f}G")

# 4. Detailed config for each VM
INTEREST_KEYS = ("name", "boot", "scsihw", "machine", "cpu", "cores", "memory", "ostype",
                 "hostpci0", "hostpci1", "usb0", "usb1", "net0", "net1", "net2")
for v in vms:
    vmid, NODE = v["vmid"], v["node"]
    cfg = call(f"/nodes/{NODE}/qemu/{vmid}/config")["data"]
    print(f"\n=== VM {vmid} {cfg.get('name','-')} (节点 {NODE}) ===")
    for k in INTEREST_KEYS:
        if k in cfg:
            print(f"  {k:<10} = {cfg[k]}")

# 5. PVE host network
for NODE in sorted({v["node"] for v in vms}):
    print(f"\n=== PVE 节点 {NODE} 网络 ===")
    for i in call(f"/nodes/{NODE}/network")["data"]:
        extras = []
        if i.get("slaves"): extras.append(f"slaves={','.join(i['slaves'])}")
        if i.get("bond_mode"): extras.append(f"mode={i['bond_mode']}")
        if i.get("bridge_ports"): extras.append(f"ports={','.join(i['bridge_ports'])}")
        print(f"  {i['iface']:<10} type={i['type']:<10} active={i['active']} "
              f"autostart={i.get('autostart','-')} addr={i.get('address','-')}/{i.get('netmask','-')} "
              f"gw={i.get('gateway','-')}  {' '.join(extras)}")
