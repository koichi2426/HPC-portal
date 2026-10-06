"""アカウント入力と初期パスワードのルール。"""

import re
import secrets

_HPC_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")
_HPC_DISPLAY_NAME_MAX_LENGTH = 80
_HPC_RANDOM_PASSWORD_LENGTH = 12
_HPC_RANDOM_PASSWORD_UPPERCASE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_HPC_RANDOM_PASSWORD_LOWERCASE = "abcdefghijklmnopqrstuvwxyz"
_HPC_RANDOM_PASSWORD_DIGITS = "0123456789"
_HPC_RANDOM_PASSWORD_ALPHABET = (
    _HPC_RANDOM_PASSWORD_UPPERCASE
    + _HPC_RANDOM_PASSWORD_LOWERCASE
    + _HPC_RANDOM_PASSWORD_DIGITS
)


def validate_username(username: str, protected_users=()) -> str | None:
    """Linuxユーザー名を検証する。

    Args:
        username: 検証対象のユーザー名。
        protected_users: 作成・変更の制限対象となるユーザー名の集合。

    Returns:
        正常ならNone、不正なら利用者向けエラーメッセージ。
    """
    name = (username or "").strip().lower()
    if not _HPC_USERNAME_RE.fullmatch(name):
        return "ユーザー名は英数字で始まり、3〜32文字の英数字・_- のみ使用できます"
    if name in protected_users or name == "root":
        return "このユーザー名は予約されています"
    return None


def validate_password(password: str) -> str | None:
    """パスワードの長さと禁止文字を検証し、不正なら理由を返す。

    Args:
        password: 設定または本人確認に使うパスワード。

    Returns:
        不正なら利用者向けの理由、問題がなければNone。
    """
    if not password or len(password) < 8:
        return "パスワードは8文字以上にしてください"
    if any(char in password for char in (":", "\n", "\r")):
        return "パスワードにコロンや改行は使用できません"
    return None


def validate_display_name(display_name: str) -> str | None:
    """Linux GECOS欄へ保存する表示名を検証する。

    Args:
        display_name: 設定する表示名。

    Returns:
        正常ならNone、不正ならエラーメッセージ。
    """
    value = (display_name or "").strip()
    if len(value) > _HPC_DISPLAY_NAME_MAX_LENGTH:
        return f"表示名は{_HPC_DISPLAY_NAME_MAX_LENGTH}文字以内にしてください"
    if any(separator in value for separator in (":", ",")) or any(
        not char.isprintable() for char in value
    ):
        return "表示名にコロン、カンマ、制御文字は使用できません"
    return None


def generate_password() -> str:
    """英大文字・英小文字・数字を各1文字以上含む初期パスワードを生成する。

    Returns:
        英大文字・英小文字・数字を含む12文字のランダムパスワード。
    """
    while True:
        password = "".join(
            secrets.choice(_HPC_RANDOM_PASSWORD_ALPHABET)
            for _ in range(_HPC_RANDOM_PASSWORD_LENGTH)
        )
        if (
            any(char in _HPC_RANDOM_PASSWORD_UPPERCASE for char in password)
            and any(char in _HPC_RANDOM_PASSWORD_LOWERCASE for char in password)
            and any(char in _HPC_RANDOM_PASSWORD_DIGITS for char in password)
        ):
            return password
