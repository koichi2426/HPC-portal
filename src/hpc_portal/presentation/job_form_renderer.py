"""外部設定やサービスに依存せず、ジョブ作成フォームを描画する。"""

import html
from dataclasses import dataclass

from jupyterhub.utils import url_escape_path, url_path_join

from hpc_portal.presentation.job_allocation import allocation_html, stop_button_html


@dataclass(frozen=True)
class JobFormSettings:
    """フォームに表示するバージョン・選択肢・公開先の設定。"""

    jupyter_ubuntu_version: str
    ollama_allowed_context_lengths: tuple[str, ...]
    ollama_allowed_cpus: tuple[str, ...]
    ollama_allowed_keep_alive: tuple[str, ...]
    ollama_allowed_kv_cache_types: tuple[str, ...]
    ollama_allowed_max_loaded_models: tuple[str, ...]
    ollama_allowed_max_queue: tuple[str, ...]
    ollama_allowed_memory: tuple[str, ...]
    ollama_allowed_parallel: tuple[str, ...]
    ollama_default_context_length: str
    ollama_default_cpus: str
    ollama_default_flash_attention: bool
    ollama_default_keep_alive: str
    ollama_default_kv_cache_type: str
    ollama_default_max_loaded_models: str
    ollama_default_max_queue: str
    ollama_default_memory: str
    ollama_default_parallel: str
    ollama_version: str
    openwebui_version: str
    public_scheme: str
    job_dns_domain: str


def select_options(
    values: tuple[str, ...], default: str, labels: dict[str, str] | None = None
) -> str:
    """許可値だけを含むselect要素用option一覧を生成する。

    Args:
        values: 選択を許可する値。
        default: 初期選択する値。
        labels: 値ごとの表示名。省略時は値をそのまま表示する。

    Returns:
        HTMLエスケープ済みのoption要素一覧。
    """
    labels = labels or {}
    return "".join(
        f'<option value="{html.escape(value, quote=True)}"'
        f"{' selected' if value == default else ''}>"
        f"{html.escape(labels.get(value, value))}</option>"
        for value in values
    )


def app_option_html(app_choice: str, recommendation: dict[str, str]) -> str:
    """推奨リソースをdata属性に含むアプリ選択肢を生成する。

    Args:
        app_choice: フォーム内部で使うアプリ名。
        recommendation: 表示名、推奨値、案内文を含む設定。

    Returns:
        HTMLエスケープ済みのoption要素。
    """
    attributes = {
        "value": app_choice,
        "data-label": recommendation["label"],
        "data-cpu": recommendation["cpu"],
        "data-memory": recommendation["memory"],
        "data-memory-label": recommendation["memory_label"],
        "data-gpu": recommendation["gpu"],
        "data-hours": recommendation["hours"],
        "data-hours-label": recommendation["hours_label"],
        "data-summary": recommendation["summary"],
        "data-guidance": recommendation["guidance"],
    }
    rendered_attributes = " ".join(
        f'{name}="{html.escape(str(value), quote=True)}"'
        for name, value in attributes.items()
    )
    label = html.escape(recommendation["label"])
    if app_choice == "open-webui":
        label += " (AI Chat)"
    elif app_choice == "shared-ollama":
        label += " (管理者専用)"
    return f"<option {rendered_attributes}>{label}</option>"


def render_options_form(
    spawner,
    *,
    resource,
    portal_admin,
    shared,
    recommendations,
    shared_ollama_gpu_label,
    static_versions,
    settings: JobFormSettings,
):
    """取得済みの表示データから本番・プレビュー共通の起動フォームを描画する。

    Args:
        spawner: 対象アプリを管理するJupyterHub Spawner。
        resource: 取得済みのCPU・メモリ・ストレージ・GPU状態。
        portal_admin: 閲覧者がポータル管理者か。
        shared: 共有Ollamaの表示情報。
        recommendations: アプリごとの推奨リソースと案内文。
        shared_ollama_gpu_label: 共有OllamaのGPU割当を示す文言。
        static_versions: 静的ファイル名とキャッシュ更新用バージョンの対応。
        settings: フォームに表示するバージョン・選択肢・公開先の設定。

    Returns:
        本番とプレビューで共通の起動フォームHTML。
    """

    def job_host(job_id):
        """ジョブIDと描画設定から、ジョブ用ホスト名を組み立てる。

        Args:
            job_id: 対象のSlurmジョブID。

        Returns:
            ジョブ公開用のホスト名。
        """
        return f"job{job_id}.{settings.job_dns_domain}"

    cpu_available = resource["cpu_available"]
    cpu_available_count = resource["cpu_available_count"]
    cpu_total = resource["cpu_total"]
    cpu_status = resource["cpu_status"]
    mem_used_gb = resource["mem_used_gb"]
    mem_gpu_used_gb = resource["mem_gpu_used_gb"]
    mem_slurm_available = resource["mem_slurm_available"]
    mem_slurm_available_gb = resource["mem_slurm_available_gb"]
    mem_slurm_used_gb = resource["mem_slurm_used_gb"]
    mem_slurm_total_gb = resource["mem_slurm_total_gb"]
    mem_slurm_status = resource["mem_slurm_status"]
    disk_available = resource["disk_available"]
    disk_available_gb = resource["disk_available_gb"]
    disk_total_gb = resource["disk_total_gb"]
    disk_status = resource["disk_status"]
    gpu_max = resource["gpu_max"]
    gpu_available = resource["gpu_available"]
    gpu_available_count = resource["gpu_available_count"]
    gpu_status = resource["gpu_status"]
    gpu_processes = resource["gpu_processes"]
    gpu_processes_available = resource["gpu_processes_available"]
    if not gpu_processes_available:
        gpu_process_count_label = "取得できません"
        gpu_process_list_html = (
            '<li class="hpc-gpu-process-empty">GPUプロセス情報を取得できません</li>'
        )
    elif gpu_processes:
        gpu_process_count_label = f"利用中 {len(gpu_processes)}件"
        gpu_process_list_html = "".join(
            '<li class="hpc-gpu-process-item">'
            f'<span class="hpc-gpu-process-name">{html.escape(str(process["name"]))}</span>'
            f'<span class="hpc-gpu-process-meta">{html.escape(str(process["username"]))} · PID {int(process["pid"])}</span>'
            "</li>"
            for process in gpu_processes
        )
    else:
        gpu_process_count_label = "利用中 0件"
        gpu_process_list_html = (
            '<li class="hpc-gpu-process-empty">GPUを使用中のプロセスはありません</li>'
        )

    active_sessions_html = ""
    user = spawner.user
    if portal_admin:
        if shared.get("active"):
            active_sessions_html += (
                '<div class="gx10-app-card" data-hpc-shared-ollama-status style="padding:12px;margin-top:10px;">'
                f'<div class="hpc-row-between">'
                f'<div class="hpc-section-title">● Ollama <span class="hpc-muted" style="font-size:11px;">(job {html.escape(str(shared.get("job_id") or ""))})</span></div>'
                f'<div class="hpc-inline-actions">'
                f'<a class="hpc-page-link" href="/hub/apps/shared-ollama">詳細 →</a>'
                f"</div></div>"
                f'<span class="hpc-muted" style="display:block;margin-top:6px;font-size:0.75rem;">割り当て: '
                f"{html.escape(str(shared['allocation']['cpu']))} vCPU · {html.escape(str(shared['allocation']['memory']))} RAM · {html.escape(str(shared['allocation']['gpu_label']))} · {html.escape(str(shared['allocation']['hours']))}</span>"
                f'<span class="hpc-app-version" style="display:block;">バージョン: '
                f"<strong data-hpc-ollama-running-version>{'v' + html.escape(str(shared.get('version') or '')) if shared.get('version') else '確認中'}</strong>"
                f'<span class="hpc-version-update" data-hpc-ollama-version-update{"" if shared.get("update_available") else " hidden"}>更新可能</span></span>'
                f"</div>"
            )
    for name, s in user.spawners.items():
        is_openwebui = (
            str((getattr(s, "user_options", None) or {}).get("app_choice", ""))
            == "open-webui"
        )
        app_label = "Open WebUI" if is_openwebui else "JupyterLab"
        jid = getattr(s, "job_id", "") or ""
        public_url = getattr(s, "public_url", "") or ""
        if is_openwebui and jid:
            url = public_url or f"{settings.public_scheme}://{job_host(jid)}/"
        elif s.active and jid:
            # 起動時のpublic_urlがHubを指したままでも、現在のserver.base_urlを優先する。
            srv = getattr(s, "server", None)
            base = getattr(srv, "base_url", None) if srv else None
            if base:
                p = str(base)
                if not p.endswith("/"):
                    p += "/"
                url = f"{settings.public_scheme}://{job_host(jid)}{p}"
            elif name:
                rel = url_path_join(user.base_url, url_escape_path(name), "/")
                url = f"{settings.public_scheme}://{job_host(jid)}{rel}"
            else:
                url = f"{settings.public_scheme}://{job_host(jid)}{user.base_url}"
        elif public_url:
            url = public_url
        elif jid:
            if name:
                rel = url_path_join(user.base_url, url_escape_path(name), "/")
            else:
                rel = user.base_url
            url = f"{settings.public_scheme}://{job_host(jid)}{rel}"
        else:
            url = f"/user/{user.name}/{name}/" if name else f"/user/{user.name}/"

        user_options = getattr(s, "user_options", None) or {}
        alloc_html = allocation_html(user_options)
        running_version = str(user_options.get("openwebui_version", ""))
        version_server_path = url_escape_path(str(name)) if name else "__default__"
        if is_openwebui:
            version_html = (
                '<span class="hpc-app-version" style="display:block;margin-top:4px;" '
                f'data-hpc-openwebui-version-url="/hub/apps/{version_server_path}/version">'
                "バージョン: "
                f"<strong data-hpc-running-version>{'v' + html.escape(running_version) if running_version else '確認中'}</strong>"
                '<span class="hpc-version-update" data-hpc-version-update hidden>再起動で更新</span></span>'
            )
        else:
            # 旧構成もUbuntu 24.04固定だったため、保存値がない既存jobは現行設定値で補完する。
            running_version = str(
                user_options.get("ubuntu_version") or settings.jupyter_ubuntu_version
            )
            update_html = (
                '<span class="hpc-version-update">再起動で更新</span>'
                if running_version
                and running_version != settings.jupyter_ubuntu_version
                else ""
            )
            version_html = (
                '<span class="hpc-app-version" style="display:block;margin-top:4px;">'
                f"環境: <strong>{'Ubuntu ' + html.escape(running_version) if running_version else '不明'}</strong>"
                f"{update_html}</span>"
            )
        stop_btn = stop_button_html(name)
        server_name_attr = html.escape(str(name or ""), quote=True)

        if getattr(s, "pending", None):
            pending_state = str(getattr(s, "pending", "spawn"))
            active_sessions_html += (
                f'<div class="gx10-app-card" data-hpc-app-status data-server-name="{server_name_attr}" '
                f'data-hpc-app-state="pending" data-hpc-reload-on-change="false" style="padding:12px;margin-top:10px;">'
                f'<div class="hpc-row-between">'
                f'<div>● {app_label} <span data-hpc-app-status-text class="hpc-status-warn">起動中（{pending_state}）</span></div>'
                f"<div>{stop_btn}</div></div>"
                f"{alloc_html}"
                f"{version_html}"
                f'<div data-hpc-app-progress class="hpc-progress hpc-progress-indeterminate" role="progressbar" aria-label="アプリを起動しています"><div class="hpc-progress-fill"></div></div>'
                f"</div>"
            )
        elif s.active:
            active_sessions_html += (
                f'<div class="gx10-app-card" style="padding:12px;margin-top:10px;">'
                f'<div class="hpc-row-between">'
                f'<div class="hpc-section-title">● {app_label}</div>'
                f'<div class="hpc-inline-actions">'
                f'<a class="hpc-page-link hpc-external-link" href="{url}" target="_blank" rel="noopener noreferrer" aria-label="JUMPを新しいタブで開く">'
                f'<span>JUMP</span><svg class="hpc-external-link-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3h7v7M10 14 21 3M21 14v7H3V3h7"/></svg></a>'
                f"{stop_btn}</div></div>"
                f"{alloc_html}{version_html}</div>"
            )

    if not active_sessions_html:
        active_sessions_html = '<div class="hpc-empty" style="font-style:italic;">No active sessions.</div>'

    app_options = [
        app_option_html("ubuntu-cli", recommendations["ubuntu-cli"]),
        app_option_html("open-webui", recommendations["open-webui"]),
    ]
    if portal_admin:
        app_options.append(
            app_option_html("shared-ollama", recommendations["shared-ollama"])
        )
    app_options_html = "".join(app_options)
    initial_recommendation = recommendations["ubuntu-cli"]
    shared_cpu_options = select_options(
        settings.ollama_allowed_cpus,
        settings.ollama_default_cpus,
        {value: f"{value} vCPU" for value in settings.ollama_allowed_cpus},
    )
    shared_memory_options = select_options(
        settings.ollama_allowed_memory,
        settings.ollama_default_memory,
        {value: f"{value} RAM" for value in settings.ollama_allowed_memory},
    )
    shared_parallel_options = select_options(
        settings.ollama_allowed_parallel, settings.ollama_default_parallel
    )
    shared_loaded_options = select_options(
        settings.ollama_allowed_max_loaded_models,
        settings.ollama_default_max_loaded_models,
    )
    shared_context_options = select_options(
        settings.ollama_allowed_context_lengths,
        settings.ollama_default_context_length,
        {
            "32768": "32K",
            "65536": "64K",
            "131072": "128K",
            "262144": "256K",
        },
    )
    shared_kv_options = select_options(
        settings.ollama_allowed_kv_cache_types,
        settings.ollama_default_kv_cache_type,
    )
    shared_keep_alive_options = select_options(
        settings.ollama_allowed_keep_alive,
        settings.ollama_default_keep_alive,
        {"5m": "5分", "30m": "30分", "1h": "1時間", "-1": "常時"},
    )
    shared_queue_options = select_options(
        settings.ollama_allowed_max_queue, settings.ollama_default_max_queue
    )
    shared_flash_options = select_options(
        ("1", "0"),
        "1" if settings.ollama_default_flash_attention else "0",
        {"1": "ON", "0": "OFF"},
    )

    # GPUを持たないノードでは選択肢を出さない。出したうえで無効化すると、
    # 選べたのに効かない状態になり原因が分からなくなる。
    if gpu_max > 0:
        gpu_field_html = (
            '<div><label class="label">GPU</label>'
            '<select class="form-control input-dark" name="gpu">'
            '<option value="0" selected>使わない</option>'
            '<option value="1">使う（全員で共有）</option></select>'
            '<span class="hpc-muted" style="display:block;margin-top:4px;font-size:0.75rem;">'
            "GPUは予約せず全員で共有します。統合メモリのためGPU専用のメモリはなく、"
            "GPUが確保した分も上のRAMから消費されます。モデルのサイズを含めて指定してください。"
            "</span></div>"
        )
    else:
        gpu_field_html = (
            '<div><label class="label">GPU</label>'
            '<input type="hidden" name="gpu" value="0">'
            '<p class="hpc-muted" style="margin:6px 0 0;font-size:0.75rem;">'
            "このノードでは利用できません</p></div>"
        )

    header_html = f"""
    <div id="resource-dashboard" data-hpc-resource-meter data-hpc-user="{html.escape(user.name, quote=True)}">
        <div class="gx10-card">
            <h3 class="hpc-page-title">gx10-ac12 Control Center</h3>
            <div class="hpc-muted hpc-spawn-resource-head" style="font-size:0.8rem;">Node: gx10-ac12 <span class="hpc-refresh-status" data-resource-refresh-status aria-live="polite"><span class="hpc-refresh-spinner" aria-hidden="true"></span><span data-resource-updated-at>最終更新 --:--:--</span><span class="visually-hidden" data-resource-refresh-live>取得中</span></span></div>
            <div class="resource-grid" aria-label="現在使えるリソース">
                <div class="resource-meter">
                    <div class="meter-head"><span>CPU 空き</span><span class="meter-status" data-resource-text="cpu_status">{cpu_status}</span></div>
                    <div class="meter-track" title="CPU 空きリソース">
                        <div class="meter-fill" data-resource-width="cpu_available" style="width:{cpu_available:.0f}%;"></div>
                    </div>
                    <div class="meter-numbers">
                        <span data-resource-text="cpu_available_count">残り {cpu_available_count:.1f} vCPU</span>
                        <span data-resource-text="cpu_total">最大 {cpu_total} vCPU</span>
                    </div>
                </div>
                <details class="resource-meter hpc-unified-memory hpc-resource-menu">
                    <summary class="hpc-unified-memory-summary" aria-label="統合メモリの説明を開く">
                        <div class="meter-head"><span class="hpc-resource-label">統合メモリ 空き</span><span class="meter-status" data-resource-text="mem_slurm_status">{mem_slurm_status}</span></div>
                        <div class="meter-track" title="Slurmが割り当て可能な統合メモリ"><div class="meter-fill" data-resource-width="mem_slurm_available" style="width:{mem_slurm_available:.0f}%;"></div></div>
                        <div class="meter-numbers"><span data-resource-text="mem_slurm_available_gb">残り {mem_slurm_available_gb:.1f} GB</span><span data-resource-text="mem_slurm_total_gb">最大 {mem_slurm_total_gb:.1f} GB</span></div>
                    </summary>
                    <div class="hpc-unified-memory-panel">
                        <strong>統合メモリについて</strong>
                        <p>CPUとGPUが共有して使用するメモリです。GPU専用VRAMはありません。</p>
                        <dl><div><dt>Slurm予約済み</dt><dd data-resource-text="mem_slurm_used_gb">{mem_slurm_used_gb:.1f} GB</dd></div><div><dt>割り当て可能</dt><dd data-resource-text="mem_slurm_available_gb">残り {mem_slurm_available_gb:.1f} GB</dd></div><div><dt>OS実使用</dt><dd data-resource-text="mem_used_gb">{mem_used_gb:.1f} GB</dd></div><div><dt>うちGPU確保分</dt><dd data-resource-text="mem_gpu_used_gb">{mem_gpu_used_gb:.1f} GB</dd></div><div><dt>最大</dt><dd data-resource-text="mem_slurm_total_gb">最大 {mem_slurm_total_gb:.1f} GB</dd></div></dl>
                        <p class="hpc-unified-memory-note">メーターはSlurmが割り当て可能な残量です。他のジョブが予約したメモリはOS上まだ未使用でも割り当てられないため、OS実使用とは一致しません。GPUが確保した分は専用VRAMではなくこの統合メモリの内数ですが、OS実使用には現れないため別に示しています。</p>
                    </div>
                </details>
                <div class="resource-meter">
                    <div class="meter-head"><span>Storage 空き</span><span class="meter-status" data-resource-text="disk_status">{disk_status}</span></div>
                    <div class="meter-track" title="Storage 空きリソース">
                        <div class="meter-fill" data-resource-width="disk_available" style="width:{disk_available:.0f}%;"></div>
                    </div>
                    <div class="meter-numbers">
                        <span data-resource-text="disk_available_gb">残り {disk_available_gb:.1f} GB</span>
                        <span data-resource-text="disk_total_gb">最大 {disk_total_gb:.1f} GB</span>
                    </div>
                </div>
                <details class="resource-meter hpc-gpu-processes">
                    <summary class="hpc-gpu-summary" aria-label="GPUを使用中のプロセスを開く">
                        <div class="meter-head"><span class="hpc-resource-label">GPU 空き</span><span class="meter-status" data-resource-text="gpu_status">{gpu_status}</span></div>
                        <div class="meter-track" title="Slurmが割り当て可能なGPU"><div class="meter-fill" data-resource-width="gpu_available" style="width:{gpu_available:.0f}%;"></div></div>
                        <div class="meter-numbers"><span data-resource-text="gpu_available_count">残り {gpu_available_count} / {gpu_max} 枚</span><span class="hpc-gpu-process-summary" data-gpu-process-count aria-live="polite">{gpu_process_count_label}</span></div>
                    </summary>
                    <ul class="hpc-gpu-process-list" data-gpu-process-list>{gpu_process_list_html}</ul>
                </details>
            </div>
            <div id="active-list">{active_sessions_html}</div>
        </div>
        <div class="gx10-card">
            <div class="form-group">
                <label class="label">App Template</label>
                <select class="form-control input-dark" name="app_choice">
                    {app_options_html}
                </select>
                <div id="app-version-help" class="hpc-app-version" style="margin-top:8px;"
                     data-ubuntu-label="Ubuntu {html.escape(settings.jupyter_ubuntu_version, quote=True)}"
                     data-openwebui-label="Open WebUI v{html.escape(settings.openwebui_version, quote=True)}"
                     data-ollama-label="Ollama v{html.escape(settings.ollama_version, quote=True)}">バージョン: Ubuntu {html.escape(settings.jupyter_ubuntu_version)}</div>
                <div id="shared-ollama-options" class="hpc-shared-options">
                    <div class="hpc-muted" style="font-size:0.78rem;margin-bottom:10px;">Ollama は共有 Slurm job として起動します。この設定は全ユーザーに共通で、停止後の次回起動時に反映されます。</div>
                    <div class="hpc-form-grid-3">
                        <div><label class="label">vCPU</label><select class="form-control input-dark" name="ollama_cpus">{shared_cpu_options}</select></div>
                        <div><label class="label">メモリ割り当て</label><select class="form-control input-dark" name="ollama_memory">{shared_memory_options}</select></div>
                        <div><label class="label">GPU</label><input type="text" class="form-control input-dark" value="{html.escape(shared_ollama_gpu_label, quote=True)}" readonly></div>
                    </div>
                    <div class="hpc-form-grid-2">
                        <div><label class="label">同時処理数</label><select class="form-control input-dark" name="ollama_parallel">{shared_parallel_options}</select></div>
                        <div><label class="label">同時ロードモデル数</label><select class="form-control input-dark" name="ollama_max_loaded_models">{shared_loaded_options}</select></div>
                    </div>
                    <div class="hpc-form-grid-2">
                        <div><label class="label">コンテキスト長</label><select class="form-control input-dark" name="ollama_context_length">{shared_context_options}</select></div>
                        <div><label class="label">KVキャッシュ</label><select class="form-control input-dark" name="ollama_kv_cache_type">{shared_kv_options}</select></div>
                    </div>
                    <details class="hpc-ollama-advanced">
                        <summary>詳細設定</summary>
                        <div class="hpc-form-grid-3">
                            <div><label class="label">モデル保持時間</label><select class="form-control input-dark" name="ollama_keep_alive">{shared_keep_alive_options}</select></div>
                            <div><label class="label">最大待機数</label><select class="form-control input-dark" name="ollama_max_queue">{shared_queue_options}</select></div>
                            <div><label class="label">Flash Attention</label><select class="form-control input-dark" name="ollama_flash_attention">{shared_flash_options}</select></div>
                        </div>
                    </details>
                    <div class="hpc-muted" style="font-size:0.74rem;margin-top:8px;">並列数とコンテキスト長を増やすと、KVキャッシュのメモリ使用量が増えます。</div>
                </div>
                <div id="standard-resource-options">
                    <div class="hpc-form-grid-2">
                        <div><label class="label">vCPUs</label><input type="number" class="form-control input-dark" name="cpu" value="2" min="1"></div>
                        <div><label class="label">RAM (GB)</label><input type="number" class="form-control input-dark" name="mem" value="4" min="1">
                            <span class="hpc-field-hint" data-mem-bump-hint role="status" aria-live="polite" hidden></span></div>
                    </div>
                    <div class="hpc-form-grid-2">
                        {gpu_field_html}
                        <div><label class="label">最大実行時間</label>
                            <select class="form-control input-dark" name="hours">
                                <option value="1">1 時間</option>
                                <option value="2" selected>2 時間</option>
                                <option value="4">4 時間</option>
                                <option value="8">8 時間</option>
                                <option value="12">12 時間</option>
                                <option value="24">24 時間</option>
                                <option value="48">48 時間</option>
                                <option value="72">72 時間</option>
                                <option value="unlimited">無制限</option>
                            </select></div>
                    </div>
                </div>
                <div id="standard-resource-help" class="hpc-muted" style="font-size:0.74rem;margin-top:-5px;">無制限は Slurm パーティションで許可された上限まで実行できます。</div>
                <section id="app-resource-recommendation" class="hpc-resource-recommendation" aria-labelledby="app-resource-recommendation-title" aria-live="polite">
                    <div class="hpc-recommendation-heading">
                        <span class="hpc-recommendation-badge">推奨リソース</span>
                        <strong id="app-resource-recommendation-title" data-recommendation-label>{initial_recommendation["label"]}</strong>
                    </div>
                    <dl class="hpc-recommendation-values" aria-label="選択中アプリの推奨割り当て">
                        <div><dt>CPU</dt><dd data-recommendation-cpu>{initial_recommendation["cpu"]} vCPU</dd></div>
                        <div><dt>RAM</dt><dd data-recommendation-memory>{initial_recommendation["memory_label"]}</dd></div>
                        <div><dt>GPU</dt><dd data-recommendation-gpu>{initial_recommendation["gpu"]}</dd></div>
                        <div><dt>時間</dt><dd data-recommendation-hours>{initial_recommendation["hours_label"]}</dd></div>
                    </dl>
                    <p class="hpc-recommendation-summary" data-recommendation-summary>{initial_recommendation["summary"]}</p>
                    <p class="hpc-recommendation-guidance" data-recommendation-guidance>{initial_recommendation["guidance"]}</p>
                </section>
            </div>
        </div>
    </div>
    """

    static_js = "".join(
        '<script src="/hub/hpc-js/{filename}?v={version}"></script>'.format(
            filename=filename,
            version=static_versions["js/" + filename],
        )
        for filename in (
            "core.js",
            "resource-meter.js",
            "app-status.js",
            "spawn-form.js",
        )
    )
    return header_html + static_js
