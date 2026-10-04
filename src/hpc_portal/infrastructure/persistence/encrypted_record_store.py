"""Root-private durable encrypted records; ciphertext is bound to record identity."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet


class EncryptedRecordStore:
    def __init__(self, directory):
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
            raise ValueError(
                "既存 DB の暗号鍵がありません。バックアップから復元してください"
            )
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
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind, name):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM records WHERE kind=? AND name=?", (kind, name)
            ).fetchone()
        if not row:
            return None
        payload = json.loads(self.cipher.decrypt(row[0]))
        if payload.pop("_record") != [kind, name]:
            raise ValueError("encrypted record identity mismatch")
        return payload

    def put(self, kind, name, payload):
        encrypted = self.cipher.encrypt(
            json.dumps({**payload, "_record": [kind, name]}).encode()
        )
        with self.connect() as db:
            db.execute(
                "INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,name) DO UPDATE SET payload=excluded.payload",
                (kind, name, encrypted),
            )

    def names(self, kind):
        with self.connect() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT name FROM records WHERE kind=? ORDER BY name", (kind,)
                )
            ]

    def delete(self, kind, name):
        with self.connect() as db:
            db.execute("DELETE FROM records WHERE kind=? AND name=?", (kind, name))
