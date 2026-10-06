"""ホスト上の待受プロセスと空きポート候補を調べ、公開先の所有者を照合する。"""

import hashlib
import os
import socket
import time
from pathlib import Path

import psutil


def candidate_id(row):
    """PIDやポートが再利用されても、別の待受プロセスを同じ候補として扱わない。"""
    identity = [row[k] for k in ("uid", "pid", "started_at", "inode", "netns", "port")]
    return hashlib.sha256(repr(identity).encode()).hexdigest()


def process_snapshot(pid, proc_root=Path("/proc")):
    proc = psutil.Process(pid)
    uid = proc.uids().real
    if proc.uids().effective != uid:
        raise ValueError("privileged process is not a publication target")
    started = proc.create_time()
    try:
        cwd = proc.cwd()
    except psutil.AccessDenied:
        cwd = ""
    # コマンド引数や環境変数には秘密値が含まれるため、候補の表示情報に使わない。
    return {
        "pid": pid,
        "uid": uid,
        "started_at": started,
        "display_name": proc.name(),
        "workdir": cwd,
        "netns": (proc_root / str(pid) / "ns/net").stat().st_ino,
    }


class LinuxListenerInventory:
    def __init__(self, config):
        self.config = config

    def sockets(self):
        # 取得失敗を空きポートと誤認しないよう、一覧取得の失敗は呼び出し元へ伝える。
        return psutil.net_connections(kind="tcp")

    def listeners(self, uid):
        if uid < self.config.min_uid:
            raise ValueError("システムユーザーのアプリは公開できません")
        host_ns = os.stat("/proc/self/ns/net").st_ino
        rows = []
        for connection in self.sockets():
            if (
                connection.status != psutil.CONN_LISTEN
                or not connection.pid
                or connection.fd < 0
            ):
                continue
            if connection.family != socket.AF_INET or connection.laddr.ip not in {
                "127.0.0.1",
                "0.0.0.0",
            }:
                continue
            port = connection.laddr.port
            if port < 1024 or port in self.config.reserved_ports:
                continue
            try:
                before = process_snapshot(connection.pid)
                if before["uid"] != uid or before["netns"] != host_ns:
                    continue
                link = os.readlink(f"/proc/{connection.pid}/fd/{connection.fd}")
                if not link.startswith("socket:["):
                    continue
                after = process_snapshot(connection.pid)
                if before != after:
                    continue
                row = {**before, "port": port, "inode": link, "fd": connection.fd}
                row["candidate"] = candidate_id(row)
                rows.append(row)
            except (psutil.Error, OSError, ValueError):
                continue
        return sorted(
            rows, key=lambda row: (-row["started_at"], row["port"], row["pid"])
        )

    def choose(self, uid, candidate="", port=None):
        rows = self.listeners(uid)
        if candidate:
            rows = [
                r
                for r in rows
                if r["candidate"] == candidate and (port is None or r["port"] == port)
            ]
        elif port is not None:
            rows = [r for r in rows if r["port"] == port]
        else:
            rows = []
        if len(rows) != 1:
            raise ValueError(
                "接続先を特定できません。候補一覧を更新して選択してください"
            )
        row = rows[0]
        matches = [
            c
            for c in self.sockets()
            if c.status == psutil.CONN_LISTEN and c.laddr.port == row["port"]
        ]
        if not matches:
            raise ValueError("接続先が終了しています")
        for match in matches:
            try:
                owner = process_snapshot(match.pid)
            except (psutil.Error, OSError, ValueError, TypeError):
                raise ValueError("このポートの所有者を確認できません") from None
            if owner["uid"] != uid or owner["netns"] != row["netns"]:
                raise ValueError("このポートの所有者が競合しています")
        return row

    def validate(self, target):
        """登録時のプロセスとソケットが、現在も同じ所有者で待ち受けているか確認する。"""
        try:
            before = process_snapshot(target["pid"])
            if any(
                before[k] != target[k] for k in ("uid", "pid", "started_at", "netns")
            ):
                raise ValueError("接続先のアプリが変更されています")
            link = os.readlink(f"/proc/{target['pid']}/fd/{target['fd']}")
            if link != target["inode"]:
                raise ValueError("接続先の待ち受けが変更されています")
            matches = [
                c
                for c in psutil.Process(target["pid"]).net_connections(kind="tcp")
                if c.fd == target["fd"]
                and c.status == psutil.CONN_LISTEN
                and c.family == socket.AF_INET
                and c.laddr.port == target["port"]
                and c.laddr.ip in {"127.0.0.1", "0.0.0.0"}
            ]
            after = process_snapshot(target["pid"])
            if not matches or any(
                after[k] != target[k] for k in ("uid", "pid", "started_at", "netns")
            ):
                raise ValueError("接続先の待ち受けが変更されています")
            return target
        except (psutil.Error, OSError, KeyError):
            raise ValueError("接続先が終了または変更されています") from None

    def ports(self, uid):
        """使用中・予約済みポートを除き、bind可能な候補を返す。予約は行わない。"""
        rows = self.listeners(uid)
        used = {c.laddr.port for c in self.sockets() if c.laddr}
        try:
            threshold = int(
                Path("/proc/sys/net/ipv4/ip_unprivileged_port_start").read_text()
            )
        except (OSError, ValueError):
            raise ValueError("ポートの権限設定を確認できません") from None
        candidates = []
        for port in range(
            max(self.config.port_start, threshold), self.config.port_end + 1
        ):
            if port in used or port in self.config.reserved_ports:
                continue
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            candidates.append(port)
            if len(candidates) >= self.config.candidate_count:
                break
        return {
            "checked_at": time.time(),
            "range": [self.config.port_start, self.config.port_end],
            "reserved": list(self.config.reserved_ports),
            "free": candidates,
            "listeners": rows,
        }
