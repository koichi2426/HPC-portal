"""未設定のローカル秘密値を安全に初期化する。"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import tempfile
from pathlib import Path


SECRET_GENERATORS = {
    "litellm_master_key": lambda: f"sk-{secrets.token_hex(32)}",
    "litellm_salt_key": lambda: secrets.token_hex(32),
    "litellm_database_password": lambda: secrets.token_hex(32),
    "searxng_secret_key": lambda: secrets.token_hex(32),
    "search_mcp_auth_token": lambda: secrets.token_urlsafe(48),
}
SECRET_LINE_PATTERN = re.compile(
    r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<separator>\s*:\s*)(?P<value>.*?)(?P<newline>\r?\n)?$"
)


def _is_unset(value: str) -> bool:
    """秘密値が未設定または雛形のままか判定する。

    Args:
        value: YAML行のコロン以降にある値。

    Returns:
        自動生成の対象ならTrue。
    """

    normalized = value.strip().strip('"\'').strip()
    return (
        normalized.lower() in {"", "null", "~"}
        or "REPLACE_WITH_" in normalized.upper()
    )


def _replace_missing_values(content: str) -> tuple[str, list[str]]:
    """対象キーの未設定値だけを置換する。

    Args:
        content: secret.ymlの内容。

    Returns:
        更新後の内容と生成したキー名の一覧。
    """

    generated: list[str] = []
    found: set[str] = set()
    output: list[str] = []

    for line in content.splitlines(keepends=True):
        match = SECRET_LINE_PATTERN.match(line)
        if match is None or match["key"] not in SECRET_GENERATORS:
            output.append(line)
            continue

        key = match["key"]
        found.add(key)
        if not _is_unset(match["value"]):
            output.append(line)
            continue

        value = SECRET_GENERATORS[key]()
        newline = match["newline"] or ""
        output.append(
            f'{match["indent"]}{key}{match["separator"]}"{value}"{newline}'
        )
        generated.append(key)

    missing = [key for key in SECRET_GENERATORS if key not in found]
    if missing:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += "\n"
        for key in missing:
            output.append(f'{key}: "{SECRET_GENERATORS[key]()}"\n')
            generated.append(key)

    return "".join(output), generated


def _write_atomically(path: Path, content: str) -> None:
    """同じディレクトリ内で秘密ファイルを原子的に置換する。

    Args:
        path: 書き込み先の実体パス。
        content: 書き込む内容。
    """

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def find_missing(path: Path) -> list[str]:
    """未設定または雛形のままの秘密キーを一覧化する（書き込みは行わない）。

    Args:
        path: secret.ymlのパス。

    Returns:
        未設定のキー名の一覧。

    Raises:
        FileNotFoundError: secret.ymlが存在しない場合。
    """

    if not path.exists():
        raise FileNotFoundError(f"秘密情報ファイルがありません: {path}")

    target = path.resolve() if path.is_symlink() else path
    content = target.read_text(encoding="utf-8")

    found: dict[str, str] = {}
    for line in content.splitlines():
        match = SECRET_LINE_PATTERN.match(line)
        if match is not None and match["key"] in SECRET_GENERATORS:
            found[match["key"]] = match["value"]

    return [
        key
        for key in SECRET_GENERATORS
        if key not in found or _is_unset(found[key])
    ]


def ensure_secrets(path: Path) -> list[str]:
    """不足する秘密値を生成し、既存値を維持する。

    Args:
        path: secret.ymlのパス。

    Returns:
        新しく生成したキー名の一覧。

    Raises:
        FileNotFoundError: secret.ymlが存在しない場合。
    """

    if not path.exists():
        raise FileNotFoundError(f"秘密情報ファイルがありません: {path}")

    target = path.resolve() if path.is_symlink() else path
    content = target.read_text(encoding="utf-8")
    updated, generated = _replace_missing_values(content)
    if generated:
        _write_atomically(target, updated)
    else:
        target.chmod(0o600)
    return generated


def main() -> int:
    """コマンドライン引数を処理して秘密値を初期化する。

    Returns:
        正常終了時は0。
    """

    parser = argparse.ArgumentParser(
        description="secret.ymlの未設定値だけを安全なランダム値で初期化します。"
    )
    parser.add_argument("path", type=Path, help="secret.ymlのパス")
    parser.add_argument(
        "--check",
        action="store_true",
        help="書き込まずに未設定の秘密値がないか確認する（デプロイ前チェック用）",
    )
    args = parser.parse_args()

    if args.check:
        missing = find_missing(args.path)
        if missing:
            print("未設定の秘密値: " + ", ".join(missing))
            print("make setup を実行して秘密値を生成してください。")
            return 1
        print("秘密値は設定済みです")
        return 0

    generated = ensure_secrets(args.path)
    if generated:
        print("生成した秘密値: " + ", ".join(generated))
    else:
        print("秘密値は設定済みです（変更なし）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
