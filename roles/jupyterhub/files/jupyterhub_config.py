"""JupyterHubへHPCポータル拡張を登録する設定エントリポイント。"""

import sys
from pathlib import Path

# Traitletsは設定ファイルの親ディレクトリをsys.pathへ追加しないため、
# CLI・systemdのどちらから起動しても同梱パッケージを読めるようにする。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hpc_portal import runtime

runtime.c = get_config()  # noqa: F821

from hpc_portal.common import (
    HPC_JOB_DNS_DOMAIN,
    HPC_PORTAL_ADMIN_USERS,
    HPC_PUBLIC_DOMAIN,
    HPC_PUBLIC_SCHEME,
    JUPYTERHUB_HUB_PORT,
    JUPYTERHUB_PORT,
    _default_subdomain_hook,
    _oauth_job_host_ctx,
    c,
)

def hpc_subdomain_hook(name, domain, kind):
    """JupyterHubのユーザーURLに利用するサブドメインを決定する。

    Args:
        name: JupyterHubが渡すユーザーまたはサービス名。
        domain: JupyterHubが計算した既定ドメイン。
        kind: user、serviceなどの対象種別。

    Returns:
        Hub用またはジョブ用のサブドメイン。
    """
    host = _oauth_job_host_ctx.get()
    if kind == "user" and host:
        return host
    if kind == "user":
        # spawn 中以外の user URL は hub ドメインへ固定し、<username>.<zone> への遷移を防ぐ
        return HPC_PUBLIC_DOMAIN
    # public_url がホスト名のみのとき domain が短くなり <username>.<host>... になるのを防ぐ
    return _default_subdomain_hook(name, HPC_JOB_DNS_DOMAIN, kind)


# ---------------------------------------------------------------------------
# 想定フロー（sequence と対応）
# フェーズ1: User→CF(<hub-subdomain>.<base-domain>)→Proxy(8000)→Hub ログイン/Spawn→Hub→Slurm sbatch→JOBID
#           →slurmd→Apptainer（動的ポートで待ち）
# フェーズ2: Hub が localhost:動的ポート へ疎通→JOBID で job<JOBID>.<base-domain> を決定
#           →CHP に「そのホスト → 127.0.0.1:ポート」を登録（HPCSlurmSpawner + proxy_spec）
# フェーズ3: User→job4.<base-domain>→CF(*.<base-domain>)→Proxy→CHP が Host でバックエンドへ転送
# ---------------------------------------------------------------------------
# 1. ネットワーク: Proxy=8000 / Hub=8081（図の gx10-ac12 上の役割分担）
c.JupyterHub.bind_url = f"http://0.0.0.0:{JUPYTERHUB_PORT}"
c.JupyterHub.hub_bind_url = f"http://127.0.0.1:{JUPYTERHUB_HUB_PORT}/hub/"
c.JupyterHub.hub_connect_url = f"http://127.0.0.1:{JUPYTERHUB_HUB_PORT}/hub/"
c.JupyterHub.hub_ip = "127.0.0.1"
c.JupyterHub.hub_connect_ip = "127.0.0.1"
# cloudflared → 127.0.0.1:8000 経由の X-Forwarded-* を信頼しないと、Proto/Port がブレて
# /hub/user/... ↔ https://gx10.../user/... のリダイレクトループになる（journal に Redirect loop が出る）
c.JupyterHub.trusted_downstream_ips = ["127.0.0.1", "::1"]
c.JupyterHub.default_url = "/hub/home"
c.JupyterHub.template_paths = ["/etc/jupyterhub/templates"]
c.JupyterHub.allow_named_servers = True
# Hubの再起動時もBatchSpawnerが投入したSlurmジョブは停止しない。
c.JupyterHub.cleanup_servers = False

c.Authenticator.allow_all = True
# Linuxユーザーを正とし、OS側で削除済みのユーザーをHub DBへ残さない。
c.Authenticator.delete_invalid_users = True

c.Authenticator.admin_users = set(HPC_PORTAL_ADMIN_USERS)

# サブドメイン方式: ゾーンは <base-domain>（job<N>.<base-domain> を CHP が受ける）
c.JupyterHub.subdomain_host = f"{HPC_PUBLIC_SCHEME}://{HPC_JOB_DNS_DOMAIN}"
# Hub のブラウザ向け URL（ログイン・ダッシュは <hub-subdomain>.<base-domain>）
c.JupyterHub.public_url = f"{HPC_PUBLIC_SCHEME}://{HPC_PUBLIC_DOMAIN}/"
c.JupyterHub.subdomain_hook = hpc_subdomain_hook
# gx10.<zone> と job<id>.<zone> 間で認証/ XSRF cookie を共有
# traitlets の LazyConfigValue では setdefault が使えないため dict を直接代入する
c.JupyterHub.tornado_settings = {
    "headers": {
        "Content-Security-Policy": f"frame-ancestors 'self' https://*.{HPC_JOB_DNS_DOMAIN}",
    },
    "cookie_options": {
        "domain": f".{HPC_JOB_DNS_DOMAIN}",
        "secure": True,
        "samesite": "lax",
    }
}

# cookie_options.domain 付き hub cookie は既定の clear_login_cookie では消えず、
# ログアウト直後に /hub/login → /hub/home へ戻る。

# 各モジュールのimport時に、Handler・Spawner・Proxy設定を順番に登録する。
# 明示した順序は依存関係（基盤→ドメイン→統合設定）を表す。
from hpc_portal import auth as _auth  # noqa: E402,F401
from hpc_portal import resources as _resources  # noqa: E402,F401
from hpc_portal import users as _users  # noqa: E402,F401
from hpc_portal import litellm as _litellm  # noqa: E402,F401
from hpc_portal import ollama as _ollama  # noqa: E402,F401
from hpc_portal import apps as _apps  # noqa: E402,F401
from hpc_portal import routing as _routing  # noqa: E402,F401
from hpc_portal import spawner as _spawner  # noqa: E402,F401
from hpc_portal import proxy as _proxy  # noqa: E402,F401
from hpc_portal import forms as _forms  # noqa: E402,F401
from hpc_portal import batch as _batch  # noqa: E402,F401
from hpc_portal.handlers import register_handlers  # noqa: E402

register_handlers()
