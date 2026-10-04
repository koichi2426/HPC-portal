"""JupyterHubのSpawner識別情報とジョブ用URL。"""

from jupyterhub.utils import url_escape_path, url_path_join

from hpc_portal.infrastructure.config.settings import HPC_JOB_DNS_DOMAIN


def _job_host(job_id: str) -> str:
    """Slurm JOBIDから公開ホスト名を作る。

    Args:
        job_id: SlurmのJOBID。

    Returns:
        ``job<JOBID>.<domain>`` 形式のホスト名。
    """
    return f"job{job_id}.{HPC_JOB_DNS_DOMAIN}"


def _spawner_job_id(spawner) -> str:
    """実行中に job_id が属性から消えるケースがあるため複数ソースから回収する

    Args:
        spawner: 対象のSpawner。

    Returns:
        SlurmジョブID。取得できない場合は空文字列。
    """
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
    """Hub が期待するプレフィックス: /user/<name>/ または named の場合はその配下

    Args:
        spawner: 対象のSpawner。

    Returns:
        対象アプリのユーザーURLパス。
    """
    u = spawner.user
    if spawner.name:
        return url_path_join(u.base_url, url_escape_path(spawner.name), "/")
    return u.base_url


def _is_openwebui_spawner(spawner) -> bool:
    """OpenWebUI テンプレート起動かどうかを判定する

    Args:
        spawner: 対象のSpawner。

    Returns:
        Open WebUI用SpawnerならTrue。
    """
    try:
        return str((spawner.user_options or {}).get("app_choice", "")) == "open-webui"
    except Exception:
        return False
