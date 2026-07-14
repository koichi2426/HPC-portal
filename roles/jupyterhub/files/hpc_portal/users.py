"""Linuxユーザーの検証、作成、削除、表示名・パスワード変更を提供する。"""

from .common import (
    HPC_PORTAL_ADMIN_USERS,
    HPC_PORTAL_GRANT_SUDO,
    HPC_PORTAL_PROTECTED_USERS,
    HPC_PORTAL_SUDO_GROUP,
    HPC_PORTAL_USER_MIN_UID,
    os,
    pwd,
    re,
    secrets,
    subprocess,
)

_HPC_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")
_HPC_NOLOGIN_SHELLS = {"/usr/sbin/nologin", "/bin/false", "/sbin/nologin"}
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


def _hpc_is_portal_admin(user) -> bool:
    """ログインユーザーがポータル管理者か判定する。

    Args:
        user: JupyterHubのユーザーオブジェクト。

    Returns:
        管理者ならTrue。
    """
    return bool(user and user.name in HPC_PORTAL_ADMIN_USERS)


def _hpc_validate_username(username: str) -> str | None:
    """Linuxユーザー名を検証する。

    Args:
        username: 検証対象のユーザー名。

    Returns:
        正常ならNone、不正なら利用者向けエラーメッセージ。
    """
    name = (username or "").strip().lower()
    if not _HPC_USERNAME_RE.fullmatch(name):
        return "ユーザー名は英数字で始まり、3〜32文字の英数字・_- のみ使用できます"
    if name in HPC_PORTAL_PROTECTED_USERS:
        return "このユーザー名は予約されています"
    return None


def _hpc_validate_password(password: str) -> str | None:
    """初期パスワードの最低要件を検証する。

    Args:
        password: 検証対象の平文パスワード。

    Returns:
        正常ならNone、不正なら利用者向けエラーメッセージ。
    """
    if not password or len(password) < 8:
        return "パスワードは8文字以上にしてください"
    if any(char in password for char in (":", "\n", "\r")):
        return "パスワードにコロンや改行は使用できません"
    return None


def _hpc_validate_display_name(display_name: str) -> str | None:
    """Linux GECOS欄へ保存する表示名を検証する。"""
    value = (display_name or "").strip()
    if len(value) > _HPC_DISPLAY_NAME_MAX_LENGTH:
        return f"表示名は{_HPC_DISPLAY_NAME_MAX_LENGTH}文字以内にしてください"
    if any(separator in value for separator in (":", ",")) or any(
        not char.isprintable() for char in value
    ):
        return "表示名にコロン、カンマ、制御文字は使用できません"
    return None


def _hpc_generate_password() -> str:
    """英大文字・英小文字・数字を各1文字以上含む12文字のパスワードを生成する。"""
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


def _hpc_linux_users_snapshot() -> list[dict]:
    """ポータル管理対象のLinuxユーザー一覧を取得する。

    Returns:
        ユーザー名、表示名、UID、ホーム、シェル、保護状態を含む辞書の一覧。
    """
    rows = []
    for entry in pwd.getpwall():
        if entry.pw_uid < HPC_PORTAL_USER_MIN_UID:
            continue
        if entry.pw_shell in _HPC_NOLOGIN_SHELLS:
            continue
        rows.append(
            {
                "username": entry.pw_name,
                "uid": entry.pw_uid,
                "display_name": entry.pw_gecos.split(",", 1)[0].strip(),
                "home": entry.pw_dir,
                "shell": entry.pw_shell,
                "protected": entry.pw_name in HPC_PORTAL_PROTECTED_USERS,
            }
        )
    rows.sort(key=lambda r: r["username"])
    return rows


def _hpc_home_storage_usage(home: str) -> tuple[int | None, str | None]:
    """ホームディレクトリが実際に使用しているストレージ量を取得する。

    Args:
        home: 集計対象のホームディレクトリ。

    Returns:
        ``(使用バイト数, エラー)``。集計は同一ファイルシステム内に限定する。
    """
    if not home or not os.path.isdir(home):
        return None, "ホームディレクトリが見つかりません"
    try:
        result = subprocess.run(
            ["du", "-s", "-x", "-B1", "--", home],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=_HPC_CMD_ENV,
        )
    except subprocess.TimeoutExpired:
        return None, "ストレージ使用量の取得がタイムアウトしました"
    except OSError:
        return None, "ストレージ使用量を取得できません"
    if result.returncode != 0:
        return None, "ストレージ使用量を取得できません"
    try:
        return int(result.stdout.split(None, 1)[0]), None
    except (IndexError, ValueError):
        return None, "ストレージ使用量を取得できません"


_HPC_CMD_ENV = {
    **os.environ,
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}


def _hpc_run_cmd(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    """固定PATHで管理コマンドを実行する。

    Args:
        cmd: シェルを介さず実行する引数配列。
        input_text: 標準入力へ渡す文字列。

    Returns:
        標準出力と標準エラーを保持する実行結果。
    """
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        env=_HPC_CMD_ENV,
    )


def _hpc_ensure_user_home(username: str) -> str | None:
    """ユーザーのホームディレクトリを必要に応じて作成する。

    Args:
        username: 対象のLinuxユーザー名。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    try:
        ent = pwd.getpwnam(username)
    except KeyError:
        return "ユーザーが見つかりません"
    home = ent.pw_dir
    if os.path.isdir(home):
        return None
    result = _hpc_run_cmd(
        ["install", "-d", "-m", "755", "-o", username, "-g", str(ent.pw_gid), home]
    )
    if result.returncode != 0:
        return (result.stderr or result.stdout or "ホームディレクトリの作成に失敗しました").strip()
    return None


def _hpc_create_linux_user(
    username: str,
    password: str,
    grant_sudo: bool,
    display_name: str = "",
) -> str | None:
    """Linuxユーザーを作成して初期パスワードを設定する。

    Args:
        username: 作成するLinuxユーザー名。
        password: 設定する初期パスワード。
        grant_sudo: sudoグループへ追加するか。
        display_name: 管理画面へ表示する任意の名前。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    display_name = (display_name or "").strip()
    display_name_err = _hpc_validate_display_name(display_name)
    if display_name_err:
        return display_name_err
    try:
        pwd.getpwnam(username)
        return "ユーザーは既に存在します"
    except KeyError:
        pass
    cmd = ["useradd", "-m", "-s", "/bin/bash"]
    if display_name:
        cmd.extend(["-c", display_name])
    if grant_sudo and HPC_PORTAL_GRANT_SUDO:
        cmd.extend(["-G", HPC_PORTAL_SUDO_GROUP])
    cmd.append(username)
    result = _hpc_run_cmd(cmd)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "useradd failed").strip()
    chpw = _hpc_run_cmd(["chpasswd"], input_text=f"{username}:{password}")
    if chpw.returncode != 0:
        _hpc_run_cmd(["userdel", "-r", username])
        return (chpw.stderr or chpw.stdout or "chpasswd failed").strip()
    err = _hpc_ensure_user_home(username)
    if err:
        return err
    return None


def _hpc_set_linux_display_name(username: str, display_name: str) -> str | None:
    """Linux GECOS欄の表示名を設定または削除する。"""
    display_name = (display_name or "").strip()
    err = _hpc_validate_display_name(display_name)
    if err:
        return err
    try:
        entry = pwd.getpwnam(username)
    except KeyError:
        return "ユーザーが見つかりません"
    if entry.pw_uid < HPC_PORTAL_USER_MIN_UID or entry.pw_shell in _HPC_NOLOGIN_SHELLS:
        return "このユーザーはポータルの管理対象ではありません"
    result = _hpc_run_cmd(["usermod", "-c", display_name, username])
    if result.returncode != 0:
        return (result.stderr or result.stdout or "表示名の変更に失敗しました").strip()
    return None


def _hpc_delete_linux_user(username: str, actor: str) -> str | None:
    """ユーザーのジョブとプロセスを停止してLinuxユーザーを削除する。

    Args:
        username: 削除対象のLinuxユーザー名。
        actor: 操作中の管理者ユーザー名。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    if username in HPC_PORTAL_PROTECTED_USERS:
        return "保護されたユーザーは削除できません"
    if username == actor:
        return "ログイン中の自分自身は削除できません"
    try:
        pwd.getpwnam(username)
    except KeyError:
        return "ユーザーが見つかりません"
    _hpc_run_cmd(["scancel", "-u", username])
    _hpc_run_cmd(["pkill", "-u", username])
    result = _hpc_run_cmd(["userdel", "-r", username])
    if result.returncode != 0:
        return (result.stderr or result.stdout or "userdel failed").strip()
    return None


def _hpc_set_linux_password(username: str, password: str) -> str | None:
    """Linuxユーザーのパスワードを再設定する。

    Args:
        username: 対象のLinuxユーザー名。
        password: 新しい平文パスワード。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    password_err = _hpc_validate_password(password)
    if password_err:
        return password_err
    try:
        pwd.getpwnam(username)
    except KeyError:
        return "ユーザーが見つかりません"
    result = _hpc_run_cmd(["chpasswd"], input_text=f"{username}:{password}")
    if result.returncode != 0:
        return (result.stderr or result.stdout or "chpasswd failed").strip()
    return None


def _hpc_verify_linux_password(
    username: str, password: str, service: str = "login"
) -> str | None:
    """PAMでログイン中ユーザーの現在のパスワードを確認する。"""
    if not password:
        return "現在のパスワードを入力してください"
    try:
        import pamela

        pamela.authenticate(username, password, service=service)
    except Exception:
        return "現在のパスワードが正しくありません"
    return None
