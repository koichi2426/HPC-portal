"""専用nftablesテーブルで、APIへの接続をloopbackのrootと所有者に制限する。"""

import asyncio
import hashlib
import json
import os
import signal

TABLE = "hpc_api_guard"
MARKER = "HPC-portal API publication:"


async def command(argv, *, stdin=None, missing_ok=False):
    """nftコマンドを実行し、タイムアウトや中止時は子プロセスごと終了させる。

    Args:
        argv: 実行するコマンドと引数の配列。
        stdin: コマンドの標準入力へ渡すバイト列。
        missing_ok: コマンドの失敗時にNoneを返して呼び出し側で再確認するか。

    Returns:
        標準出力の文字列。失敗を許容した場合はNone。

    Raises:
        ValueError: 許容していないコマンド失敗を検出した場合。
        TimeoutError: 10秒以内にコマンドが終了しない場合。
        asyncio.CancelledError: 呼び出し元が処理を中止した場合。
    """
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
        # 呼び出し側が中止しても、子プロセスが遅れて保護設定を変更しないよう終了を待つ。
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
    """ポートと所有者を照合し、専用nftablesテーブルの更新スクリプトを作る。

    Args:
        targets: 保護する接続先の一覧。
        existing: 専用nftablesテーブルを置き換える場合はTrue。

    Returns:
        更新スクリプトと、現在の設定との比較に使うフィンガープリントの組。

    Raises:
        ValueError: 所有者・ポートの範囲が不正、または同じポートの所有者が競合する場合。
    """
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

    # 保存順序によらず同じ値を作り、変更がない場合のルール再適用を避ける。
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
        """保護記録と待受調査の依存を保持し、ルール更新のロックを準備する。

        Args:
            store: 認証情報・公開設定・ポート保護のレコード保存先。
            inventory: 公開対象の待受プロセスが登録時と同一か確認する接続先。
        """
        self.store, self.inventory = store, inventory
        self.lock = asyncio.Lock()

    def entries(self):
        """保存済みのポート保護対象を列挙する。

        Returns:
            保護対象の待受情報一覧。
        """
        return [self.store.get("guards", name) for name in self.store.names("guards")]

    async def table(self):
        """専用nftablesテーブルの管理マーカーを確認して取得する。

        Returns:
            管理テーブルの情報。未作成ならNone。

        Raises:
            ValueError: 専用テーブルの管理マーカーを確認できない場合。
        """
        raw = await command(
            ["nft", "-j", "list", "table", "inet", TABLE], missing_ok=True
        )
        if raw is None:
            # テーブル未作成と、権限不足などでnft自体を実行できない状態を区別する。
            await command(["nft", "-j", "list", "tables"])
            return None
        data = json.loads(raw)
        table = next((r["table"] for r in data["nftables"] if "table" in r), None)
        if not table or not table.get("comment", "").startswith(MARKER):
            raise ValueError("API 保護用テーブルの所有を確認できません")
        return table

    async def _apply(self):
        """保存済みの保護対象を、必要な場合だけnftablesへ一括反映する。"""
        existing = await self.table()
        script, fingerprint = ruleset(self.entries(), existing is not None)
        if existing and existing.get("comment") == MARKER + fingerprint:
            return
        # 保護が途切れないよう、専用テーブルだけを1回のトランザクションで置き換える。
        await command(["nft", "-f", "-"], stdin=script.encode())

    async def protect(self, target):
        """接続先の同一性と既存所有者を確認し、ポートの直接接続を制限する。

        Args:
            target: 登録時の所有者・プロセス・ソケットを含む接続先情報。

        Raises:
            ValueError: 接続先や保護設定の所有者が不一致、またはルールを反映できない場合。
        """
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
        """指定候補の保護設定が保存され、nftablesへ反映できるか確認する。

        Args:
            target: 登録時の所有者・プロセス・ソケットを含む接続先情報。

        Raises:
            ValueError: 接続先の保護記録がない、またはルールを確認・反映できない場合。
        """
        async with self.lock:
            entry = self.store.get("guards", str(target["port"]))
            if not entry or entry["candidate"] != target["candidate"]:
                raise ValueError("公開先の保護設定がありません")
            await self._apply()

    async def reconcile(self):
        """終了した待受の保護記録を整理し、現在の対象をnftablesへ反映する。"""
        async with self.lock:
            for target in self.entries():
                try:
                    await asyncio.to_thread(self.inventory.validate, target)
                except ValueError:
                    # 登録プロセス終了後も、同じユーザーの待受が残る間はポートを保護する。
                    # 公開先をそのworkerへ自動で付け替えることはしない。
                    listeners = await asyncio.to_thread(
                        self.inventory.listeners, target["uid"]
                    )
                    if not any(row["port"] == target["port"] for row in listeners):
                        self.store.delete("guards", str(target["port"]))
            await self._apply()
