"""静的ファイルの内容に基づくキャッシュ更新用バージョン。"""

import hashlib
import logging
import os

HPC_PORTAL_JS_DIR = "/etc/jupyterhub/static/hpc-portal-js"
HPC_PORTAL_JS_FILES = (
    "core.js",
    "resource-meter.js",
    "app-status.js",
    "app-detail.js",
    "admin-apps.js",
    "admin-users.js",
    "ollama-admin.js",
    "llm-api.js",
    "external-api.js",
    "password.js",
    "spawn-form.js",
)
HPC_PORTAL_CSS = "/etc/jupyterhub/static/hpc-portal.css"


def static_file_version(path):
    """静的ファイルの内容からキャッシュ更新用バージョンを生成する。

    Args:
        path: ハッシュ値を計算する静的ファイルのパス。

    Returns:
        SHA-256の先頭12桁。ファイルを読めない場合は ``missing``。
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as static_file:
            for chunk in iter(lambda: static_file.read(65536), b""):
                digest.update(chunk)
    except OSError:
        logging.getLogger("jupyterhub.hpc-static").warning(
            "静的ファイルのハッシュを計算できません: %s", path
        )
        return "missing"
    return digest.hexdigest()[:12]


class _HpcStaticVersions:
    """参照時点の静的ファイルからバージョン値を返す。"""

    def __init__(self, paths):
        """静的ファイル名と配置先パスの対応を保持する。

        Args:
            paths: 静的ファイル名をキー、配置先パスを値とする辞書。
        """
        self._paths = dict(paths)

    def __getitem__(self, name):
        """指定された静的ファイルの現在のバージョン値を返す。

        Args:
            name: バージョン値を取得する静的ファイル名。

        Returns:
            対象ファイルの内容から生成した短縮SHA-256。
        """
        return static_file_version(self._paths[name])


HPC_STATIC_VERSIONS = _HpcStaticVersions(
    {
        "portal_css": HPC_PORTAL_CSS,
        **{
            f"js/{filename}": os.path.join(HPC_PORTAL_JS_DIR, filename)
            for filename in HPC_PORTAL_JS_FILES
        },
    }
)
