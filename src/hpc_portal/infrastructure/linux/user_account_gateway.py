"""Linuxユーザーの検証、作成、削除、権限・表示名・パスワード変更を提供する。"""

import grp
import os
import pwd
import subprocess

from hpc_portal.domain.accounts.account_policy import (
    validate_display_name,
    validate_password,
)
from hpc_portal.domain.accounts.account_policy import (
    validate_username as validate_username_policy,
)
from hpc_portal.infrastructure.config.settings import (
    HPC_PORTAL_ADMIN_USERS,
    HPC_PORTAL_PROTECTED_USERS,
    HPC_PORTAL_SUDO_GROUP,
    HPC_PORTAL_USER_MIN_UID,
)

_HPC_NOLOGIN_SHELLS = {"/usr/sbin/nologin", "/bin/false", "/sbin/nologin"}


def is_portal_admin(user) -> bool:
    """ログインユーザーがポータル管理者か判定する。

    Args:
        user: JupyterHubのユーザーオブジェクト。

    Returns:
        管理者ならTrue。
    """
    return bool(user and user.name in HPC_PORTAL_ADMIN_USERS)


def linux_users_snapshot() -> list[dict]:
    """ポータル管理対象のLinuxユーザー一覧を取得する。

    Returns:
        ユーザー名、表示名、UID、ホーム、シェル、保護状態、sudo状態を含む辞書の一覧。
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
                "sudo_enabled": user_has_sudo(entry.pw_name),
            }
        )
    rows.sort(key=lambda r: r["username"])
    return rows


def user_has_sudo(username: str) -> bool:
    """ユーザーが設定されたsudoグループに所属するか判定する。

    Args:
        username: 確認対象のLinuxユーザー名。

    Returns:
        プライマリまたは補助グループとしてsudoグループに所属すればTrue。
    """
    try:
        entry = pwd.getpwnam(username)
        sudo_group = grp.getgrnam(HPC_PORTAL_SUDO_GROUP)
        return sudo_group.gr_gid in os.getgrouplist(username, entry.pw_gid)
    except (KeyError, OSError):
        return False


def home_storage_usage(home: str) -> tuple[int | None, str | None]:
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


def run_cmd(
    cmd: list[str],
    *,
    input_text: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """固定PATHで管理コマンドを実行する。

    Args:
        cmd: シェルを介さず実行する引数配列。
        input_text: 標準入力へ渡す文字列。
        timeout: コマンドを待つ最大秒数。Noneの場合は制限しない。

    Returns:
        標準出力と標準エラーを保持する実行結果。
    """
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=_HPC_CMD_ENV,
    )


def ensure_user_home(username: str) -> str | None:
    """ユーザーのホームディレクトリを0700で準備する。

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
    if not os.path.isdir(home):
        result = run_cmd(
            ["install", "-d", "-m", "0700", "-o", username, "-g", str(ent.pw_gid), home]
        )
        if result.returncode != 0:
            return (
                result.stderr
                or result.stdout
                or "ホームディレクトリの作成に失敗しました"
            ).strip()
    try:
        os.chmod(home, 0o700)
    except OSError:
        return "ホームディレクトリの権限を設定できません"
    return None


def create_linux_user(
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
    display_name_err = validate_display_name(display_name)
    if display_name_err:
        return display_name_err
    try:
        pwd.getpwnam(username)
        return "ユーザーは既に存在します"
    except KeyError:
        pass
    cmd = ["useradd", "-m", "-K", "HOME_MODE=0700", "-s", "/bin/bash"]
    if display_name:
        cmd.extend(["-c", display_name])
    if grant_sudo:
        cmd.extend(["-G", HPC_PORTAL_SUDO_GROUP])
    cmd.append(username)
    result = run_cmd(cmd)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "useradd failed").strip()
    chpw = run_cmd(["chpasswd"], input_text=f"{username}:{password}")
    if chpw.returncode != 0:
        run_cmd(["userdel", "-r", username])
        return (chpw.stderr or chpw.stdout or "chpasswd failed").strip()
    err = ensure_user_home(username)
    if err:
        run_cmd(["userdel", "-r", username])
        return err
    return None


def set_linux_sudo(username: str, enabled: bool) -> str | None:
    """Linuxユーザーのsudoグループ所属を変更する。

    Args:
        username: 変更対象のLinuxユーザー名。
        enabled: Trueならsudoを付与し、Falseなら解除する。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    try:
        entry = pwd.getpwnam(username)
        grp.getgrnam(HPC_PORTAL_SUDO_GROUP)
    except KeyError:
        return "ユーザーまたはsudoグループが見つかりません"
    if entry.pw_uid < HPC_PORTAL_USER_MIN_UID or entry.pw_shell in _HPC_NOLOGIN_SHELLS:
        return "このユーザーはポータルの管理対象ではありません"
    current = user_has_sudo(username)
    if current == enabled:
        return None
    if enabled:
        result = run_cmd(["usermod", "-a", "-G", HPC_PORTAL_SUDO_GROUP, username])
    else:
        result = run_cmd(["gpasswd", "-d", username, HPC_PORTAL_SUDO_GROUP])
    if result.returncode != 0:
        return (
            result.stderr
            or result.stdout
            or (
                "sudo権限の付与に失敗しました"
                if enabled
                else "sudo権限の解除に失敗しました"
            )
        ).strip()
    if user_has_sudo(username) != enabled:
        return "sudoグループの変更結果を確認できませんでした"
    return None


def set_linux_display_name(username: str, display_name: str) -> str | None:
    """Linux GECOS欄の表示名を設定または削除する。

    Args:
        username: 対象のLinuxユーザー名。
        display_name: 設定する表示名。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    display_name = (display_name or "").strip()
    err = validate_display_name(display_name)
    if err:
        return err
    try:
        entry = pwd.getpwnam(username)
    except KeyError:
        return "ユーザーが見つかりません"
    if entry.pw_uid < HPC_PORTAL_USER_MIN_UID or entry.pw_shell in _HPC_NOLOGIN_SHELLS:
        return "このユーザーはポータルの管理対象ではありません"
    result = run_cmd(["usermod", "-c", display_name, username])
    if result.returncode != 0:
        return (result.stderr or result.stdout or "表示名の変更に失敗しました").strip()
    return None


def delete_linux_user(username: str, actor: str) -> str | None:
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
    run_cmd(["scancel", "-u", username])
    run_cmd(["pkill", "-u", username])
    result = run_cmd(["userdel", "-r", username])
    if result.returncode != 0:
        return (result.stderr or result.stdout or "userdel failed").strip()
    return None


def set_linux_password(username: str, password: str) -> str | None:
    """Linuxユーザーのパスワードを再設定する。

    Args:
        username: 対象のLinuxユーザー名。
        password: 新しい平文パスワード。

    Returns:
        正常ならNone、失敗時はエラーメッセージ。
    """
    password_err = validate_password(password)
    if password_err:
        return password_err
    try:
        pwd.getpwnam(username)
    except KeyError:
        return "ユーザーが見つかりません"
    result = run_cmd(["chpasswd"], input_text=f"{username}:{password}")
    if result.returncode != 0:
        return (result.stderr or result.stdout or "chpasswd failed").strip()
    return None


def verify_linux_password(
    username: str, password: str, service: str = "login"
) -> str | None:
    """PAMでログイン中ユーザーの現在のパスワードを確認する。

    Args:
        username: 対象のLinuxユーザー名。
        password: 確認する平文パスワード。
        service: PAM認証で使用するサービス名。

    Returns:
        認証成功ならTrueとNone、失敗時はFalseとエラーの組。
    """
    if not password:
        return "現在のパスワードを入力してください"
    try:
        import pamela

        pamela.authenticate(username, password, service=service)
    except Exception:
        return "現在のパスワードが正しくありません"
    return None


class LinuxUserAccountGateway:
    def getpwnam(self, username):
        return pwd.getpwnam(username)

    def linux_users_snapshot(self, *args, **kwargs):
        return linux_users_snapshot(*args, **kwargs)

    def user_has_sudo(self, *args, **kwargs):
        return user_has_sudo(*args, **kwargs)

    def home_storage_usage(self, *args, **kwargs):
        return home_storage_usage(*args, **kwargs)

    def ensure_user_home(self, *args, **kwargs):
        return ensure_user_home(*args, **kwargs)

    def create_linux_user(self, *args, **kwargs):
        return create_linux_user(*args, **kwargs)

    def set_linux_sudo(self, *args, **kwargs):
        return set_linux_sudo(*args, **kwargs)

    def set_linux_display_name(self, *args, **kwargs):
        return set_linux_display_name(*args, **kwargs)

    def delete_linux_user(self, *args, **kwargs):
        return delete_linux_user(*args, **kwargs)

    def set_linux_password(self, *args, **kwargs):
        return set_linux_password(*args, **kwargs)

    def verify_linux_password(self, *args, **kwargs):
        return verify_linux_password(*args, **kwargs)


def validate_username(username):
    return validate_username_policy(username, HPC_PORTAL_PROTECTED_USERS)
