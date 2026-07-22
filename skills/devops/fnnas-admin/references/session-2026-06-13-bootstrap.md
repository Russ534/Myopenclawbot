# 2026-06-13 — Bootstrap session, what worked and what bit me

Real transcript notes from the first end-to-end SSH bootstrap of hermes → fnOS. Use as a worked example and to confirm the gotchas in SKILL.md are real, not theoretical.

## Environment confirmed

- Host: `192.168.31.201`, hostname `RussNAS` (matches user handle)
- Kernel: `6.18.18-trim` (fnOS marker)
- Container: hostname `hermes`, uid 10000 hermes, no sudo, no docker socket
- Container net ns inode: `net:[4026534561]`, **differs** from PID 1
- Container IP `192.168.144.2`, gateway `192.168.144.1` (docker-bridge view, NOT host view)
- Host reachable from container at 0.12ms (loopback-like latency)
- fnOS web UI: `http://192.168.31.201:5666` (200, login page) and `:80` open
- SSH: `:22` open

## What bit me — three real issues

### 1. fnOS web terminal strips newlines from multi-line paste

I gave the user a 3-line bash block:

```bash
mkdir -p /root/.ssh && chmod 700 /root/.ssh
echo 'ssh-ed25519 AAAA... hermes@...' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

The user pasted it as one block. The web terminal **collapsed the newlines into spaces**, so the actual run became:

```bash
mkdir -p /root/.ssh && chmod 700 /root/.ssh echo 'ssh-ed25519 AAAA...' ... chmod 600 /root/.ssh/authorized_keys
```

That parses as: `mkdir -p /root/.ssh && chmod 700 /root/.ssh echo 'ssh-...'` — the `echo` becomes a positional arg to `chmod`, which is a no-op (extra arg ignored). Result: `authorized_keys` was NEVER created.

User had to re-paste line-by-line, and only then did `cat ~/.ssh/authorized_keys` show the key.

**Fix baked into SKILL.md**: always tell user to paste one line at a time, OR include a verification `cat` at the end and tell the user to paste the output back so we can confirm the key landed.

### 2. `service ssh` vs `service sshd` on fnOS

I first told the user to run `systemctl status sshd` to check the daemon. fnOS (Debian-based) uses `ssh.service` (note: `ssh`, not `sshd`). `systemctl status sshd` returns `Unit sshd.service could not be found`.

User knew the right service name and used `systemctl status ssh` instead — output showed `ssh.service` active, PID 1513, `OpenBSD Secure Shell server`.

**Fix baked into SKILL.md**: tell user explicitly `systemctl status ssh` (not `sshd`). This matches Debian-derivative conventions but is easy to mistake for systemd-forked "sshd" naming.

### 3. `PermitRootLogin no` is fnOS default

Even after the key landed, the agent's `ssh root@192.168.31.201` got `Permission denied (publickey,password)`. The user ran:

```bash
grep -E "^(PermitRootLogin|PubkeyAuthentication|PasswordAuthentication|AuthorizedKeysFile)" /etc/ssh/sshd_config
```

Result: only `PermitRootLogin no` printed (other lines were commented out, which is the Debian default for `PubkeyAuthentication yes` and `PasswordAuthentication yes`).

Fix was the standard 4-step:

```bash
cp -a /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.<TIMESTAMP>
sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
grep -n '^PermitRootLogin' /etc/ssh/sshd_config
sshd -t && echo "config OK" && systemctl reload ssh
```

User ran it; output showed `33:PermitRootLogin yes`. After reload, agent-side test was pending when session interrupted (cronjob kicked in).

## User workflow preferences confirmed this session

- Prefers **paste-and-paste-back** rhythm over agent-runs-then-summarizes. The user does the typing on the host, the agent reads the output.
- Short imperative Chinese. "把命令给我复制粘贴" — exact phrasing.
- Does NOT want the agent to pre-bundle diagnostic + fix into a single command. Wants step-by-step with verification between.
- Willing to do manual SSH on host as long as the agent is clear about which command to run, what it does, and what success looks like.
- Comfortable with `sed` / `cp` / `systemctl reload` — not a Linux novice.

## Outstanding

- Reload of sshd was issued; agent-side `ssh root@192.168.31.201` re-test not yet confirmed (session interrupted).
- Docker socket (C-plan) NOT enabled — by mutual agreement, hold off.
- Original task "清理飞牛不用的网段" — host stack audit not yet run. After SSH is confirmed, run the `ip -br addr ; ip route show table all` probe.
