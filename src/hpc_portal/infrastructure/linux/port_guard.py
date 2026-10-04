"""Owner-only loopback protection in a dedicated nftables table."""

import asyncio
import hashlib
import json
import os
import signal

TABLE = "hpc_api_guard"
MARKER = "HPC-portal API publication:"


async def command(argv, *, stdin=None, missing_ok=False):
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), 10)
    except (TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    if proc.returncode:
        if missing_ok:
            return None
        raise ValueError("内部接続の保護を設定できません。管理設定を確認してください")
    return out.decode(errors="replace")


def ruleset(targets, existing=False):
    ports = {}
    for row in targets:
        port, uid = row["port"], row["uid"]
        if (
            type(port) is not int
            or not 1024 <= port <= 65535
            or type(uid) is not int
            or uid < 1000
        ):
            raise ValueError("invalid firewall target")
        if port in ports and ports[port] != uid:
            raise ValueError("ポートの所有者が競合しています")
        ports[port] = uid
    fingerprint = hashlib.sha256(json.dumps(sorted(ports.items())).encode()).hexdigest()
    lines = [f"delete table inet {TABLE}"] if existing else []
    lines += [
        f'add table inet {TABLE} {{ comment "{MARKER}{fingerprint}"; }}',
        f"add chain inet {TABLE} input {{ type filter hook input priority -5; policy accept; }}",
        f"add chain inet {TABLE} output {{ type filter hook output priority -5; policy accept; }}",
    ]
    for port, uid in sorted(ports.items()):
        lines += [
            f'add rule inet {TABLE} input iifname != "lo" ct direction original tcp dport {port} reject with tcp reset',
            f'add rule inet {TABLE} output oifname "lo" ct direction original tcp dport {port} meta skuid != {{ 0, {uid} }} reject with tcp reset',
        ]
    return "\n".join(lines) + "\n", fingerprint


class NftablesPortGuard:
    def __init__(self, store, inventory):
        self.store, self.inventory = store, inventory
        self.lock = asyncio.Lock()

    def entries(self):
        return [self.store.get("guards", name) for name in self.store.names("guards")]

    async def table(self):
        raw = await command(
            ["nft", "-j", "list", "table", "inet", TABLE], missing_ok=True
        )
        if raw is None:
            # Distinguish absent table from permission/runtime failures.
            await command(["nft", "-j", "list", "tables"])
            return None
        data = json.loads(raw)
        table = next((r["table"] for r in data["nftables"] if "table" in r), None)
        if not table or not table.get("comment", "").startswith(MARKER):
            raise ValueError("API 保護用テーブルの所有を確認できません")
        return table

    async def _apply(self):
        existing = await self.table()
        script, fingerprint = ruleset(self.entries(), existing is not None)
        if existing and existing.get("comment") == MARKER + fingerprint:
            return
        # One nft transaction replaces only this table, without an unprotected interval.
        await command(["nft", "-f", "-"], stdin=script.encode())

    async def protect(self, target):
        async with self.lock:
            await asyncio.to_thread(self.inventory.validate, target)
            previous = self.store.get("guards", str(target["port"]))
            if previous and previous["uid"] != target["uid"]:
                try:
                    await asyncio.to_thread(self.inventory.validate, previous)
                except ValueError:
                    pass
                else:
                    raise ValueError("ポートの保護設定が競合しています")
            self.store.put("guards", str(target["port"]), target)
            await self._apply()

    async def check(self, target):
        async with self.lock:
            entry = self.store.get("guards", str(target["port"]))
            if not entry or entry["candidate"] != target["candidate"]:
                raise ValueError("公開先の保護設定がありません")
            await self._apply()

    async def reconcile(self):
        async with self.lock:
            for target in self.entries():
                try:
                    await asyncio.to_thread(self.inventory.validate, target)
                except ValueError:
                    # A selected worker can exit while sibling workers still listen.
                    # Keep the port protected, without ever relinking the publication.
                    listeners = await asyncio.to_thread(
                        self.inventory.listeners, target["uid"]
                    )
                    if not any(row["port"] == target["port"] for row in listeners):
                        self.store.delete("guards", str(target["port"]))
            await self._apply()
