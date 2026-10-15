"""所有者以外が読めないSQLiteへ、レコードの識別情報と秘密値を暗号化して保存する。"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet


class EncryptedRecordStore:
    def __init__(self, directory):
        """保存先の権限を検証し、暗号鍵とSQLiteの保存領域を準備する。

        Args:
            directory: SQLite DBと暗号鍵を保存する非公開ディレクトリ。

        Raises:
            ValueError: 保存先・鍵・DBの所有者や権限が不適切、または既存DBの鍵が見つからない場合。
        """
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (
            directory.is_symlink()
            or directory.stat().st_uid != os.geteuid()
            or directory.stat().st_mode & 0o077
        ):
            raise ValueError("credential directory must be private")

        key_path = directory / "key"
        db_path = directory / "state.sqlite"
        if not key_path.exists() and db_path.exists():
            # DBが残る場合は鍵を作り直さず、復元が必要な状態として止める。
            raise ValueError(
                "既存 DB の暗号鍵がありません。バックアップから復元してください"
            )

        # 排他的に作成し、既存の鍵を上書きして保存済みデータを読めなくしない。
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
        if (
            key_path.is_symlink()
            or key_path.stat().st_uid != os.geteuid()
            or key_path.stat().st_mode & 0o077
        ):
            raise ValueError("credential key must be private")

        self.cipher = Fernet(key_path.read_bytes())
        fd = os.open(db_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_uid != os.geteuid():
                raise ValueError("credential database owner mismatch")
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)

        self.path = db_path
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT, name TEXT, payload BLOB NOT NULL, PRIMARY KEY(kind,name))"
            )

    @contextmanager
    def connect(self):
        """SQLite接続を開き、成功時は確定し失敗時は巻き戻して接続を閉じる。

        Yields:
            トランザクション内で利用するSQLite接続。
        """
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind, name):
        """暗号化レコードを読み込み、保存キーとの一致を確認して復号する。

        Args:
            kind: credentials・publications・guardsなどのレコード種別。
            name: 種類ごとに一意なレコードの保存キー。

        Returns:
            復号したレコード。未登録ならNone。

        Raises:
            ValueError: 復号した内容とレコードの種類・保存キーが一致しない場合。
        """
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM records WHERE kind=? AND name=?", (kind, name)
            ).fetchone()
        if not row:
            return None

        payload = json.loads(self.cipher.decrypt(row[0]))
        # 正しい暗号文でも、別ユーザー・別種類のレコードへ移された場合は拒否する。
        if payload.pop("_record") != [kind, name]:
            raise ValueError("encrypted record identity mismatch")
        return payload

    def put(self, kind, name, payload):
        """種類と名前も暗号文へ含め、レコードを保存または置き換える。

        Args:
            kind: credentials・publications・guardsなどのレコード種別。
            name: 種類ごとに一意なレコードの保存キー。
            payload: 暗号化して保存するJSON互換のレコード。
        """
        encrypted = self.cipher.encrypt(
            json.dumps({**payload, "_record": [kind, name]}).encode()
        )
        with self.connect() as db:
            db.execute(
                "INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,name) DO UPDATE SET payload=excluded.payload",
                (kind, name, encrypted),
            )

    def names(self, kind):
        """指定した種類の保存キーを名前順で取得する。

        Args:
            kind: credentials・publications・guardsなどのレコード種別。

        Returns:
            保存されているレコード名の一覧。
        """
        with self.connect() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT name FROM records WHERE kind=? ORDER BY name", (kind,)
                )
            ]

    def delete(self, kind, name):
        """指定した種類・名前のレコードを削除する。

        Args:
            kind: credentials・publications・guardsなどのレコード種別。
            name: 種類ごとに一意なレコードの保存キー。
        """
        with self.connect() as db:
            db.execute("DELETE FROM records WHERE kind=? AND name=?", (kind, name))
