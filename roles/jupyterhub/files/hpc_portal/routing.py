"""アプリ用サブドメインとCHPルートの同期処理を提供する。"""

from .apps import _is_openwebui_spawner, _job_host, _job_user_path, _spawner_job_id
from .common import HPC_PUBLIC_DOMAIN, HPC_PUBLIC_SCHEME, asyncio, time, urlparse


def _hpc_public_alias_routespec_for(spawner) -> str:
    """User.url が gx10 のときでも CHP が転送できるよう、Hub 公開ホスト + 同一パスの routespec"""
    if getattr(spawner, "server", None) and getattr(spawner.server, "base_url", None):
        path = str(spawner.server.base_url)
        if not path.endswith("/"):
            path += "/"
    else:
        path = _job_user_path(spawner)
    return f"{HPC_PUBLIC_DOMAIN}{path}"


def _hpc_norm_routespec(rs: str) -> str:
    """CHP の routes キーと spawner.proxy_spec の表記ゆれ（先頭 /・末尾 /）を吸収して比較用に正規化"""
    s = (rs or "").strip()
    if s.startswith("/"):
        s = s[1:]
    if s.endswith("/"):
        s = s[:-1]
    return s


def _hpc_chp_target_ready(host_url):
    """batchspawner が一瞬 http://nodename:0 を載せる間に CHP へ登録しない（503 防止）"""
    if not host_url or not isinstance(host_url, str):
        return False
    u = host_url if "://" in host_url else f"http://{host_url}"
    try:
        p = urlparse(u)
    except Exception:
        return False
    if not p.hostname:
        return False
    if p.port is None or p.port <= 0:
        return False
    return True


def _hpc_spawner_target_host(spawner) -> str:
    """CHP 転送先 URL を取得（server.host 優先、なければ port/JOBID から補完）"""
    srv = getattr(spawner, "server", None)
    h = getattr(srv, "host", None) if srv else None
    if _hpc_chp_target_ready(h):
        return h
    p = getattr(spawner, "port", 0) or 0
    try:
        p = int(p)
    except Exception:
        p = 0
    if p <= 0:
        jid = _spawner_job_id(spawner)
        if jid.isdigit():
            p = 20000 + (int(jid) % 20000)
    if p > 0:
        return f"http://127.0.0.1:{p}"
    return ""


async def _wait_for_tcp_port(host: str, port: int, timeout: float = 120.0) -> bool:
    """TCPポートが接続可能になるまで待機する。

    Args:
        host: 接続先ホスト。
        port: 接続先ポート。
        timeout: 最大待機秒数。

    Returns:
        接続できた場合はTrue、タイムアウト時はFalse。
    """
    import asyncio, time

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            await asyncio.sleep(2.0)
    return False


async def _hpc_wait_chp_target(spawner, timeout: float = 30.0, step: float = 0.2):
    """起動直後の一時的な :0 を避け、CHP へ登録可能な host:port を待つ"""
    deadline = time.perf_counter() + timeout
    last = None
    while time.perf_counter() < deadline:
        srv = getattr(spawner, "server", None)
        h = getattr(srv, "host", None) if srv else None
        last = h
        if _hpc_chp_target_ready(h):
            return h
        await asyncio.sleep(step)
    return last


def _sync_job_proxy_and_public(spawner) -> None:
    """JOBID 確定後: CHP の routespec を job<id>.domain/user/... にし、単一ユーザー公開 URL も同期"""
    jid = _spawner_job_id(spawner)
    if not jid:
        return
    host = _job_host(jid)
    if _is_openwebui_spawner(spawner):
        # OpenWebUI は job サブドメイン直下で公開
        path = "/"
    else:
        # 実際に singleuser が掲げているパス（ORM）を優先し、spawn 時の古い public_url とズレないようにする
        if getattr(spawner, "server", None) and getattr(spawner.server, "base_url", None):
            path = str(spawner.server.base_url)
            if not path.endswith("/"):
                path += "/"
        else:
            path = _job_user_path(spawner)
    # ホストルーティング時の routespec は「host/path/」（先頭に / を付けない）
    spawner.proxy_spec = f"{host}{path}"
    spawner.public_url = f"{HPC_PUBLIC_SCHEME}://{host}{path}"

