"""ポータル上のアプリ表示とアプリ詳細情報を組み立てる。"""

from .common import (
    BaseHandler,
    HPC_JOB_DNS_DOMAIN,
    HPC_PUBLIC_SCHEME,
    c,
    html,
    secrets,
    time,
    url_escape_path,
    url_path_join,
    web,
)
from .ollama import _hpc_shared_ollama_detail_context
from .resources import _hpc_resource_snapshot
from .users import _hpc_is_portal_admin


class HpcNewApplicationHandler(BaseHandler):
    """`/hub/new` を常に新規 named server の spawn 画面へ誘導する"""

    @web.authenticated
    async def get(self):
        """一意なnamed server名を生成して起動画面へ転送する。"""
        user = self.current_user
        user_name = user.escaped_name
        # 既存 server に吸われないよう、毎回ユニークな server 名を採番する
        server_name = f"app-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        target = url_path_join(
            self.hub.base_url,
            "spawn",
            url_escape_path(user_name),
            url_escape_path(server_name),
        )
        self.redirect(target)


c.JupyterHub.extra_handlers = [(r"/new", HpcNewApplicationHandler)]


class HpcAppDetailHandler(BaseHandler):
    """起動中アプリの詳細（割り当てリソース・停止・JUMP）"""

    @web.authenticated
    async def get(self, server_name_path):
        """起動中アプリの詳細画面を表示する。

        Args:
            server_name_path: URLに含まれるnamed server名。

        Raises:
            web.HTTPError: shared Ollamaを一般ユーザーが開いた場合。
        """
        user = self.current_user
        server_name = _hpc_server_name_from_path(server_name_path)
        if server_name == "shared-ollama":
            if not _hpc_is_portal_admin(user):
                raise web.HTTPError(403, "管理者のみアクセスできます")
            detail = _hpc_shared_ollama_detail_context()
            html = await self.render_template(
                "app_detail.html",
                user=user,
                detail=detail,
                node_resources=_hpc_resource_snapshot(),
                hpc_public_scheme=HPC_PUBLIC_SCHEME,
                hpc_job_dns_domain=HPC_JOB_DNS_DOMAIN,
            )
            self.finish(html)
            return
        spawner = user.spawners.get(server_name)
        if spawner is None or not (
            getattr(spawner, "active", False) or getattr(spawner, "pending", None)
        ):
            self.redirect(url_path_join(self.hub.base_url, "home"))
            return
        detail = _hpc_spawner_detail_context(spawner, server_name, user)
        try:
            html = await self.render_template(
                "app_detail.html",
                user=user,
                detail=detail,
                # template_vars の hpc_resource_snapshot（関数）と名前が衝突しないよう別名で渡す
                node_resources=_hpc_resource_snapshot(),
                hpc_public_scheme=HPC_PUBLIC_SCHEME,
                hpc_job_dns_domain=HPC_JOB_DNS_DOMAIN,
            )
        except Exception:
            self.log.exception(
                "HPC: app_detail render failed for %s", server_name
            )
            raise
        self.finish(html)


def _job_host(job_id: str) -> str:
    """Slurm JOBIDから公開ホスト名を作る。

    Args:
        job_id: SlurmのJOBID。

    Returns:
        ``job<JOBID>.<domain>`` 形式のホスト名。
    """
    return f"job{job_id}.{HPC_JOB_DNS_DOMAIN}"


def _spawner_job_id(spawner) -> str:
    """実行中に job_id が属性から消えるケースがあるため複数ソースから回収する"""
    jid = getattr(spawner, "job_id", "") or ""
    if jid:
        return str(jid)
    jid = getattr(spawner, "_hpc_job_id", "") or ""
    if jid:
        return str(jid)
    try:
        st = spawner.get_state() or {}
        jid = st.get("job_id", "") or st.get("jobid", "")
        if jid:
            return str(jid)
    except Exception:
        pass
    return ""


def _job_user_path(spawner) -> str:
    """Hub が期待するプレフィックス: /user/<name>/ または named の場合はその配下"""
    u = spawner.user
    if spawner.name:
        return url_path_join(u.base_url, url_escape_path(spawner.name), "/")
    return u.base_url


def _is_openwebui_spawner(spawner) -> bool:
    """OpenWebUI テンプレート起動かどうかを判定する"""
    try:
        return str((spawner.user_options or {}).get("app_choice", "")) == "open-webui"
    except Exception:
        return False


def _hpc_runtime_hours_label(runtime: str) -> str:
    """Slurm 形式の実行時間 (HH:MM:SS / UNLIMITED) を表示用に短縮する"""
    rt = str(runtime or "").strip()
    if rt.upper() in ("UNLIMITED", "INFINITE"):
        return "無制限"
    try:
        parts = rt.split(":")
        if parts:
            return f"{int(parts[0])}h"
    except (TypeError, ValueError):
        pass
    return rt or "—"


def _hpc_runtime_from_hours_choice(hours_value: str) -> tuple[str, str]:
    """起動フォームの hours 値から (runtime, #SBATCH 行) を生成する"""
    raw = str(hours_value or "").strip().lower()
    if raw in ("unlimited", "infinite", "none", "0"):
        return "UNLIMITED", "#SBATCH --time=UNLIMITED"
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        hours = 8
    hours = max(1, min(hours, 9999))
    runtime = f"{hours:02d}:00:00"
    return runtime, f"#SBATCH --time={runtime}"


def _hpc_allocation_summary(user_options) -> dict:
    """起動時に要求した Slurm リソース割り当てを表示用 dict にまとめる"""
    uo = user_options or {}
    app_choice = str(uo.get("app_choice", "ubuntu-cli"))
    if app_choice == "open-webui":
        app_label = "Open WebUI"
    elif app_choice in ("jupyterlab", "jupyter"):
        app_label = "JupyterLab"
    else:
        app_label = app_choice.replace("-", " ").title() or "Application"
    try:
        gpu_n = int(uo.get("gpu", "0") or 0)
    except (TypeError, ValueError):
        gpu_n = 0
    cpu = str(uo.get("nprocs", "—"))
    mem = str(uo.get("memory", "—"))
    runtime = str(uo.get("runtime", "—"))
    hours = _hpc_runtime_hours_label(runtime)
    gpu_label = f"{gpu_n} GPU" if gpu_n > 0 else "GPU なし"
    line = f"{cpu} vCPU · {mem} RAM · {gpu_label} · {hours}"
    return {
        "app_label": app_label,
        "cpu": cpu,
        "memory": mem,
        "gpu": gpu_n,
        "gpu_label": gpu_label,
        "runtime": runtime,
        "hours": hours,
        "line": line,
    }


def _hpc_allocation_html(user_options) -> str:
    """割り当てリソースの HTML 行（spawn フォーム・一覧用）"""
    a = _hpc_allocation_summary(user_options)
    return (
        f'<span class="hpc-muted" style="display:block;margin-top:6px;font-size:0.75rem;'
        f'letter-spacing:0.02em;">割り当て: {a["line"]}</span>'
    )


def _hpc_stop_button_html(server_name: str) -> str:
    """named server 停止ボタン（data-server-name 空 = デフォルト server）"""
    sn = html.escape(str(server_name or ""), quote=True)
    return (
        f'<button type="button" class="gx10-stop-btn" data-server-name="{sn}" '
        f'onclick="hpcStopServer(this)" '
        f'title="Slurmジョブを終了し、このアプリを削除します">停止</button>'
    )


def _hpc_server_name_to_path(server_name: str) -> str:
    """URL パス用（空名は __default__）"""
    return str(server_name or "") or "__default__"


def _hpc_server_name_from_path(path_segment: str) -> str:
    """URLパス要素からnamed server名を復元する。

    Args:
        path_segment: URLエンコード済みのパス要素。

    Returns:
        デコード済みのserver名。
    """
    return "" if path_segment == "__default__" else path_segment


def _hpc_spawner_job_url(spawner, server_name: str, user) -> str:
    """アプリへ JUMP する公開 URL（home.html と同じ優先順位）"""
    uo = getattr(spawner, "user_options", None) or {}
    is_openwebui = str(uo.get("app_choice", "")) == "open-webui"
    jid = _spawner_job_id(spawner) or getattr(spawner, "job_id", "") or ""
    public_url = getattr(spawner, "public_url", "") or ""
    if is_openwebui and jid:
        return public_url or f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}/"
    if getattr(spawner, "active", False) and jid:
        srv = getattr(spawner, "server", None)
        base = getattr(srv, "base_url", None) if srv else None
        if base:
            p = str(base)
            if not p.endswith("/"):
                p += "/"
            return f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{p}"
        if server_name:
            rel = url_path_join(user.base_url, url_escape_path(server_name), "/")
            return f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{rel}"
        return f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{user.base_url}"
    if public_url:
        return public_url
    if jid and getattr(spawner, "pending", None):
        if server_name:
            rel = url_path_join(user.base_url, url_escape_path(server_name), "/")
        else:
            rel = user.base_url
        return f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{rel}"
    if server_name:
        return url_path_join(f"/user/{user.name}", url_escape_path(server_name), "/")
    return url_path_join(f"/user/{user.name}", "/")


def _hpc_spawner_detail_context(spawner, server_name: str, user) -> dict:
    """アプリ詳細画面用の表示データ"""
    uo = getattr(spawner, "user_options", None) or {}
    alloc = _hpc_allocation_summary(uo)
    jid = _spawner_job_id(spawner) or getattr(spawner, "job_id", "") or ""
    pending = getattr(spawner, "pending", None)
    active = bool(getattr(spawner, "active", False))
    if pending:
        status = "pending"
        status_label = f"起動中 ({pending})"
    elif active:
        status = "running"
        status_label = "実行中"
    else:
        status = "unknown"
        status_label = "不明"
    port = getattr(spawner, "port", None) or ""
    app_choice = str(uo.get("app_choice", "ubuntu-cli"))
    return {
        "server_name": server_name,
        "server_name_path": _hpc_server_name_to_path(server_name),
        "app_label": alloc["app_label"],
        "app_choice": app_choice,
        "status": status,
        "status_label": status_label,
        "pending": pending,
        "active": active,
        "job_id": jid,
        "job_url": _hpc_spawner_job_url(spawner, server_name, user),
        "job_host": _job_host(jid) if jid else "",
        "allocation": alloc,
        "port": port,
        "public_url": getattr(spawner, "public_url", "") or "",
        "proxy_spec": getattr(spawner, "proxy_spec", "") or "",
    }


HPC_STOP_SERVER_JS = """
function hpcReadXsrf() {
    var m = document.cookie.match(/(?:^|; )_xsrf=([^;]*)/);
    return m ? decodeURIComponent(m[1]) : "";
}
function hpcStopServer(btn) {
    if (!btn || btn.disabled) return;
    var serverName = btn.getAttribute("data-server-name") || "";
    if (!confirm("このアプリケーションを停止・削除しますか？\\n関連する Slurm ジョブも終了します。")) return;
    var user = window.HPC_HUB_USER;
    if (!user) {
        alert("ユーザー情報を取得できません");
        return;
    }
    var xsrf = hpcReadXsrf();
    var api = serverName
        ? "/hub/api/users/" + encodeURIComponent(user) + "/servers/" + encodeURIComponent(serverName)
        : "/hub/api/users/" + encodeURIComponent(user) + "/server";
    btn.disabled = true;
    btn.textContent = "停止中…";
    fetch(api, {
        method: "DELETE",
        credentials: "same-origin",
        headers: xsrf ? { "X-XSRFToken": xsrf } : {}
    })
        .then(function (r) {
            if (!r.ok && r.status !== 202 && r.status !== 204) throw new Error("stop failed");
            window.location.reload();
        })
        .catch(function () {
            btn.disabled = false;
            btn.textContent = "停止";
            alert("停止に失敗しました。しばらくしてから再度お試しください。");
        });
}
"""
