# 2026-06-13 (cont. 2) — bond1 primary-network flip, executed end-to-end

Worked example of the "Flipping primary interface" procedure in SKILL.md. Read this
before doing any metric flip on a bond/dual-NIC host — there are two real artifacts
that the procedure does not mention and that the agent will encounter.

## What the user asked

> 让 192.168.31.201 (bond1) 成为主网络，ens18 备用。所有出站流量走 bond1。

Scope: network-layer only. Do NOT touch the listener bindings of any service
(no "bind nginx to bond1", no "restart docker on bond1"). User explicitly said
(a) on a 3-tier plan and rejected the bundled EEE tweak (see SKILL.md planning rule).

## Procedure executed (with timestamps and the actual output)

### Phase 0 — backup

```bash
# Timestamp suffix for restore
TS=$(date +%Y%m%d-%H%M%S)  # got 20260613-123600

# 5 nmconnection files → time-stamped copies
cd /etc/NetworkManager/system-connections/
for f in *.nmconnection; do cp -a "$f" "${f}.bak.${TS}"; done
# Also: cp -a /etc/iproute2/rt_tables /etc/iproute2/rt_tables.bak.${TS}
```

Note: `/etc/iproute2/` accumulates many `rt_tables.<random>` files from network_service
restarts. Don't try to clean those up — they're temp files left by trim.

### Phase 1 — flip metrics

```bash
nmcli con mod bond1 ipv4.route-metric 0
nmcli con mod "Wired connection 1" ipv4.route-metric 100
nmcli con up bond1
nmcli con up "Wired connection 1"
```

### Two artifacts to expect and NOT panic about

**Artifact A — `bond1 linkdown` for ~1 second during `nmcli con up`**

The verbose `ip route` output right after the `nmcli con up bond1` shows:

```
default via 192.168.31.1 dev bond1 proto static linkdown
```

This is normal. nmcli re-negociates the bond master, slaves are briefly
unassociated, and the kernel marks the bond "linkdown" until the slaves come
back. By the time the next command runs (within ~1s), the route line shows
`default via 192.168.31.1 dev bond1` without `linkdown`, the slaves are
`MII Status: up / Speed: 2500 Mbps`, and LACP actor/partner PDU exchange
resumes. Ping 201/210/31.1 all succeed.

**Pitfall: do not rollback at this point.** Re-running `nmcli con up bond1`
or restarting NetworkManager at the moment when `bond1 linkdown` is showing
will look like it broke the bond. Wait 2-3s and re-check.

**Artifact B — `ip rule` 10/11 entries disappear after `nmcli con up`**

Before flip:
```
0:    from all lookup local
10:   from 192.168.31.201 lookup route_bond1 proto static
11:   from 192.168.31.210 lookup route_ens18 proto static
32766: from all lookup main
32767: from all lookup default
```

After flip:
```
0:    from all lookup local
32766: from all lookup main
32767: from all lookup default
```

The 10/11 policy rules are gone. They're normally re-pushed by the network_service
when it sees a new bond1 metric, but the order of `nmcli con up` here dropped
them. Net effect: all traffic now goes through `main` table. `main` has
`default via 192.168.31.1 dev bond1` (metric 0) and `default via 192.168.31.1
dev ens18` (metric 100), so outbound still prefers bond1.

**Is this OK?** Functionally yes — the SSH session stays connected to 201, all
outbound goes bond1, return packets come back via the same route table, no
asymmetry. But: any process that previously relied on the per-source-IP
policy rule (e.g. a service bound to 210 that wanted return traffic on
ens18) loses that guarantee.

**To restore the 10/11 rules manually** (only if the user cares):
```bash
ip rule add from 192.168.31.201 lookup route_bond1 priority 10
ip rule add from 192.168.31.210 lookup route_ens18 priority 11
```
This is also NOT persistent across reboot by itself — trim's network_service
pushes these on every interface-up. So either:
- (a) leave them out, accept main-table-based routing (the documented new state)
- (b) re-add them and accept that the next trim restart will re-push them anyway

I went with (a) for this user. Confirm with them before doing otherwise.

### Phase 2 — delete orphan connection

```bash
# Verify the slave NIC isn't independently holding an IP first
ip -br addr show enx6c1ff761711d   # should show UP with no IPv4
nmcli con del "Wired connection 2"
```

If the slave is somehow holding an IP (e.g. because the bond master was not
yet fully up), the deletion will yank the IP and bond1 will lose a slave for
~1s. Confirm `ip -br addr show` first.

### Phase 3 — verify outbound actually goes bond1

The meaningful check is the *source IP* of outbound traffic:

```bash
# inside the host
curl -s --max-time 5 --interface bond1 https://ifconfig.me
curl -s --max-time 5 --interface ens18 https://ifconfig.me
ip route get 1.1.1.1
```

Expected: bond1 returns the 201 / bond1's IPv6 suffix; ens18 returns 210 /
ens18's IPv6 suffix; `ip route get 1.1.1.1` says `via 192.168.31.1 dev bond1
src 192.168.31.201`. If all three match, the flip is real.

## Rollback — one liner

```bash
ssh fnos '
  nmcli con mod bond1 ipv4.route-metric 100
  nmcli con mod "Wired connection 1" ipv4.route-metric 0
  nmcli con up "Wired connection 1"
'
```

No service restart, no interface bounce. The orphan `Wired connection 2` is
already gone (we want it gone); if rolling back past the deletion, the user
must re-create it manually from backup.

To restore the full pre-flip state from backup (rarely needed):

```bash
# from /opt/data/home/russnas/2026-06-13-bond1-primary.md
cp /etc/NetworkManager/system-connections/*.bak.20260613-123600 \
   /etc/NetworkManager/system-connections/
ssh fnos 'nmcli con reload; nmcli con up bond1; nmcli con up "Wired connection 1"'
```

## What I observed about the user's reaction

User picked option (a) "minimum touch" from a 3-tier plan without hesitation
and pushed back the moment I bundled an unrelated EEE tweak into tier B. The
planning rule in SKILL.md is there because of this interaction. **The
takeaway: when presenting a tiered plan, every option should differ only in
the scope of the *same* change, not bundle orthogonal optimizations.**

Also: the user accepted that the `ip rule 10/11` disappeared. They cared
about *outcome* ("bond1 is primary") not *mechanism* ("policy routing table
X is exactly as it was"). Don't waste tokens explaining the artifact B side
effect in detail when the user just wants "is it working?".

## Watchdog script left on the host

`/usr/local/bin/bond1-watchdog.sh` — checks `/proc/net/bonding/bond1` for
"MII Status: up" and at least 2 slaves, writes alert to syslog if not. Cron
not yet installed (user did not approve). Pending next session.
