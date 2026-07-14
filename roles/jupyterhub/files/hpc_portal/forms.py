"""アプリ起動フォームの生成と入力値変換を提供する。"""

from .apps import (
    HPC_STOP_SERVER_JS,
    _hpc_allocation_html,
    _hpc_runtime_from_hours_choice,
    _hpc_stop_button_html,
    _is_openwebui_spawner,
    _job_host,
)
from .common import (
    HPC_GPU_COUNT,
    HPC_OLLAMA_ALLOWED_CPUS,
    HPC_OLLAMA_ALLOWED_MEMORY,
    HPC_OLLAMA_DEFAULT_CPUS,
    HPC_OLLAMA_DEFAULT_MEMORY,
    HPC_OLLAMA_VERSION,
    HPC_JUPYTER_UBUNTU_VERSION,
    HPC_OPENWEBUI_VERSION,
    HPC_PUBLIC_SCHEME,
    c,
    html,
    url_escape_path,
    url_path_join,
)
from .ollama import _hpc_shared_ollama_detail_context
from .resources import _hpc_resource_snapshot
from .users import _hpc_is_portal_admin


# 2. カスタムフォーム生成
def make_options_form(spawner):
    """Spawnerの状態に合わせてアプリ起動フォームを生成する。

    Args:
        spawner: フォームを表示するSpawner。

    Returns:
        JupyterHubへ渡すHTML文字列。
    """
    disk_path = getattr(spawner, "notebook_dir", "") or getattr(spawner, "homedir", "") or "/home"
    resource = _hpc_resource_snapshot(disk_path)
    cpu_available = resource["cpu_available"]
    cpu_available_count = resource["cpu_available_count"]
    cpu_total = resource["cpu_total"]
    cpu_status = resource["cpu_status"]
    mem_available = resource["mem_available"]
    mem_available_gb = resource["mem_available_gb"]
    mem_total_gb = resource["mem_total_gb"]
    mem_status = resource["mem_status"]
    disk_available = resource["disk_available"]
    disk_available_gb = resource["disk_available_gb"]
    disk_total_gb = resource["disk_total_gb"]
    disk_status = resource["disk_status"]
    gpu_max = resource["gpu_max"]
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
    if _hpc_is_portal_admin(user):
        shared = _hpc_shared_ollama_detail_context()
        if shared.get("active"):
            active_sessions_html += (
                f'<div class="gx10-app-card" data-hpc-shared-ollama-status style="padding:12px;margin-top:10px;">'
                f'<div class="hpc-row-between">'
                f'<div class="hpc-section-title">● Ollama <span class="hpc-muted" style="font-size:11px;">(job {html.escape(str(shared.get("job_id") or ""))})</span></div>'
                f'<div class="hpc-inline-actions">'
                f'<a class="hpc-page-link" href="/hub/apps/shared-ollama">詳細 →</a>'
                f'</div></div>'
                f'<span class="hpc-muted" style="display:block;margin-top:6px;font-size:0.75rem;">割り当て: '
                f'{html.escape(str(shared["allocation"]["cpu"]))} vCPU · {html.escape(str(shared["allocation"]["memory"]))} RAM · 1 GPU · {html.escape(str(shared["allocation"]["hours"]))}</span>'
                f'<span class="hpc-app-version" style="display:block;">起動中: '
                f'<strong data-hpc-ollama-running-version>{"v" + html.escape(str(shared.get("version") or "")) if shared.get("version") else "確認中"}</strong> · '
                f'新規起動: <strong data-hpc-ollama-target-version>v{html.escape(str(shared.get("target_version") or HPC_OLLAMA_VERSION))}</strong>'
                f'<span class="hpc-version-update" data-hpc-ollama-version-update{"" if shared.get("update_available") else " hidden"}>再起動で更新</span></span>'
                f'</div>'
            )
    for name, s in user.spawners.items():
        is_openwebui = _is_openwebui_spawner(s)
        app_label = "Open WebUI" if is_openwebui else "JupyterLab"
        jid = getattr(s, "job_id", "") or ""
        public_url = getattr(s, "public_url", "") or ""
        if is_openwebui and jid:
            # OpenWebUI は job サブドメイン直下で開く
            url = public_url or f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}/"
        # spawn 時に渡された public_url が gx10 側のまま残ることがあるため active+JOBID では ORM の base_url を優先
        elif s.active and jid:
            srv = getattr(s, "server", None)
            base = getattr(srv, "base_url", None) if srv else None
            if base:
                p = str(base)
                if not p.endswith("/"):
                    p += "/"
                url = f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{p}"
            elif name:
                rel = url_path_join(user.base_url, url_escape_path(name), "/")
                url = f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{rel}"
            else:
                url = f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{user.base_url}"
        elif public_url:
            url = public_url
        elif jid:
            if name:
                rel = url_path_join(user.base_url, url_escape_path(name), "/")
            else:
                rel = user.base_url
            url = f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}{rel}"
        else:
            url = f"/user/{user.name}/{name}/" if name else f"/user/{user.name}/"

        user_options = getattr(s, "user_options", None) or {}
        alloc_html = _hpc_allocation_html(user_options)
        running_version = str(user_options.get("openwebui_version", ""))
        version_server_path = url_escape_path(str(name)) if name else "__default__"
        if is_openwebui:
            version_html = (
                '<span class="hpc-app-version" style="display:block;margin-top:4px;" '
                f'data-hpc-openwebui-version-url="/hub/apps/{version_server_path}/version">'
                '<span data-hpc-running-version-label>起動時</span>: '
                f'<strong data-hpc-running-version>{"v" + html.escape(running_version) if running_version else "確認中"}</strong> · '
                f'新規起動: <strong data-hpc-target-version>v{html.escape(HPC_OPENWEBUI_VERSION)}</strong>'
                '<span class="hpc-version-update" data-hpc-version-update hidden>再起動で更新</span></span>'
            )
        else:
            # 旧構成もUbuntu 24.04固定だったため、保存値がない既存jobは現行設定値で補完する。
            running_version = str(
                user_options.get("ubuntu_version") or HPC_JUPYTER_UBUNTU_VERSION
            )
            update_html = (
                '<span class="hpc-version-update">再起動で更新</span>'
                if running_version and running_version != HPC_JUPYTER_UBUNTU_VERSION
                else ""
            )
            version_html = (
                '<span class="hpc-app-version" style="display:block;margin-top:4px;">'
                f'起動時: <strong>{"Ubuntu " + html.escape(running_version) if running_version else "不明"}</strong> · '
                f'新規起動: <strong>Ubuntu {html.escape(HPC_JUPYTER_UBUNTU_VERSION)}</strong>'
                f'{update_html}</span>'
            )
        stop_btn = _hpc_stop_button_html(name)
        server_name_attr = html.escape(str(name or ""), quote=True)

        if getattr(s, "pending", None):
            pending_state = str(getattr(s, "pending", "spawn"))
            active_sessions_html += (
                f'<div class="gx10-app-card" data-hpc-app-status data-server-name="{server_name_attr}" '
                f'data-hpc-app-state="pending" data-hpc-reload-on-change="false" style="padding:12px;margin-top:10px;">'
                f'<div class="hpc-row-between">'
                f'<div>● {app_label} <span data-hpc-app-status-text class="hpc-status-warn">起動中（{pending_state}）</span></div>'
                f'<div>{stop_btn}</div></div>'
                f'{alloc_html}'
                f'{version_html}'
                f'<div data-hpc-app-progress class="hpc-progress hpc-progress-indeterminate" role="progressbar" aria-label="アプリを起動しています"><div class="hpc-progress-fill"></div></div>'
                f'</div>'
            )
        elif s.active:
            active_sessions_html += (
                f'<div class="gx10-app-card" style="padding:12px;margin-top:10px;">'
                f'<div class="hpc-row-between">'
                f'<div class="hpc-section-title">● {app_label}</div>'
                f'<div class="hpc-inline-actions">'
                f'<a class="hpc-page-link" href="{url}" target="_blank">JUMP ↗</a>'
                f'{stop_btn}</div></div>'
                f'{alloc_html}{version_html}</div>'
            )

    if not active_sessions_html:
        active_sessions_html = '<div class="hpc-empty" style="font-style:italic;">No active sessions.</div>'

    is_portal_admin = _hpc_is_portal_admin(user)
    shared_ollama_option = '<option value="shared-ollama">Ollama (管理者専用)</option>' if is_portal_admin else ''
    shared_cpu_options = "".join(
        f'<option value="{html.escape(v)}"{" selected" if v == HPC_OLLAMA_DEFAULT_CPUS else ""}>{html.escape(v)} vCPU</option>'
        for v in HPC_OLLAMA_ALLOWED_CPUS
    )
    shared_memory_options = "".join(
        f'<option value="{html.escape(v)}"{" selected" if v == HPC_OLLAMA_DEFAULT_MEMORY else ""}>{html.escape(v)} RAM</option>'
        for v in HPC_OLLAMA_ALLOWED_MEMORY
    )

    header_html = f"""
    <div id="resource-dashboard" data-hpc-resource-meter>
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
                        <div class="meter-head"><span class="hpc-resource-label">統合メモリ 空き</span><span class="meter-status" data-resource-text="mem_status">{mem_status}</span></div>
                        <div class="meter-track" title="統合メモリの空きリソース"><div class="meter-fill" data-resource-width="mem_available" style="width:{mem_available:.0f}%;"></div></div>
                        <div class="meter-numbers"><span data-resource-text="mem_available_gb">残り {mem_available_gb:.1f} GB</span><span data-resource-text="mem_total_gb">最大 {mem_total_gb:.1f} GB</span></div>
                    </summary>
                    <div class="hpc-unified-memory-panel">
                        <strong>統合メモリについて</strong>
                        <p>CPUとGPUが共有して使用するメモリです。GPU専用VRAMはありません。</p>
                        <dl><div><dt>使用中</dt><dd data-resource-text="mem_used_gb">{max(0, mem_total_gb - mem_available_gb):.1f} GB</dd></div><div><dt>空き</dt><dd data-resource-text="mem_available_gb">残り {mem_available_gb:.1f} GB</dd></div><div><dt>最大</dt><dd data-resource-text="mem_total_gb">最大 {mem_total_gb:.1f} GB</dd></div></dl>
                        <p class="hpc-unified-memory-note">Slurmで指定するメモリは上限です。起動時に全容量が消費されるわけではありません。</p>
                        {'<a href="/hub/home#running-applications">起動中アプリの割当を見る →</a>' if is_portal_admin else ''}
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
                    <summary><span>GPU</span><span class="hpc-gpu-process-summary" data-gpu-process-count aria-live="polite">{gpu_process_count_label}</span></summary>
                    <ul class="hpc-gpu-process-list" data-gpu-process-list>{gpu_process_list_html}</ul>
                </details>
            </div>
            <div id="active-list">{active_sessions_html}</div>
        </div>
        <div class="gx10-card">
            <div class="form-group">
                <label class="label">App Template</label>
                <select class="form-control input-dark" name="app_choice">
                    <option value="ubuntu-cli">Ubuntu CLI (JupyterLab)</option>
                    <option value="open-webui">Open WebUI (AI Chat)</option>
                    {shared_ollama_option}
                </select>
                <div id="app-version-help" class="hpc-app-version" style="margin-top:8px;"
                     data-ubuntu-label="Ubuntu {html.escape(HPC_JUPYTER_UBUNTU_VERSION, quote=True)}"
                     data-openwebui-label="Open WebUI v{html.escape(HPC_OPENWEBUI_VERSION, quote=True)}"
                     data-ollama-label="Ollama v{html.escape(HPC_OLLAMA_VERSION, quote=True)}">新規起動: Ubuntu {html.escape(HPC_JUPYTER_UBUNTU_VERSION)}</div>
                <div id="shared-ollama-options" class="hpc-shared-options">
                    <div class="hpc-muted" style="font-size:0.78rem;margin-bottom:10px;">Ollama は hpc-ollama ユーザーの共有 Slurm job として起動します。GPU は 1 固定です。</div>
                    <div class="hpc-form-grid-3">
                        <div><label class="label">Ollama vCPUs</label><select class="form-control input-dark" name="ollama_cpus">{shared_cpu_options}</select></div>
                        <div><label class="label">Ollama RAM</label><select class="form-control input-dark" name="ollama_memory">{shared_memory_options}</select></div>
                        <div><label class="label">Ollama GPUs</label><input type="text" class="form-control input-dark" value="1" readonly></div>
                    </div>
                </div>
                <div id="standard-resource-options">
                    <div class="hpc-form-grid-2">
                        <div><label class="label">vCPUs</label><input type="number" class="form-control input-dark" name="cpu" value="2" min="1"></div>
                        <div><label class="label">RAM (GB)</label><input type="number" class="form-control input-dark" name="mem" value="4" min="1"></div>
                    </div>
                    <div class="hpc-form-grid-2">
                        <div><label class="label">GPUs</label><input type="number" class="form-control input-dark" name="gpu" value="0" min="0" max="{gpu_max}"></div>
                        <div><label class="label">最大実行時間</label>
                            <select class="form-control input-dark" name="hours">
                                <option value="1">1 時間</option>
                                <option value="2">2 時間</option>
                                <option value="4">4 時間</option>
                                <option value="8" selected>8 時間</option>
                                <option value="12">12 時間</option>
                                <option value="24">24 時間</option>
                                <option value="48">48 時間</option>
                                <option value="72">72 時間</option>
                                <option value="unlimited">無制限</option>
                            </select></div>
                    </div>
                </div>
                <div id="standard-resource-help" class="hpc-muted" style="font-size:0.74rem;margin-top:-5px;">無制限は Slurm パーティションで許可された上限まで実行できます。</div>
            </div>
        </div>
    </div>
    """

    hub_user_js = html.escape(user.name, quote=True)
    js_code = (
        f"""
    <script>
    window.HPC_HUB_USER = "{hub_user_js}";
"""
        + HPC_STOP_SERVER_JS
        + """
    document.addEventListener("DOMContentLoaded", function() {
        const form = document.querySelector('form');
        const appChoice = document.querySelector('select[name="app_choice"]');
        const sharedBox = document.getElementById("shared-ollama-options");
        const standardBox = document.getElementById("standard-resource-options");
        const standardHelp = document.getElementById("standard-resource-help");
        const appVersionHelp = document.getElementById("app-version-help");
        function refreshAppChoice() {
            if (!appChoice) return;
            const isSharedOllama = appChoice.value === "shared-ollama";
            if (sharedBox) sharedBox.style.display = isSharedOllama ? "block" : "none";
            if (standardBox) {
                standardBox.style.display = isSharedOllama ? "none" : "block";
                standardBox.querySelectorAll("input, select, textarea, button").forEach(function(el) {
                    el.disabled = isSharedOllama;
                });
            }
            if (standardHelp) standardHelp.style.display = isSharedOllama ? "none" : "block";
            if (appVersionHelp) {
                const labelKey = appChoice.value === "open-webui"
                    ? "openwebuiLabel"
                    : (isSharedOllama ? "ollamaLabel" : "ubuntuLabel");
                appVersionHelp.textContent = "新規起動: " + appVersionHelp.dataset[labelKey];
            }
        }
        if (appChoice) {
            appChoice.addEventListener("change", refreshAppChoice);
            refreshAppChoice();
        }
        if (form) {
            form.onsubmit = function(ev) {
                if (appChoice && appChoice.value === "shared-ollama") {
                    ev.preventDefault();
                    const xsrf = hpcReadXsrf();
                    const cpus = document.querySelector('select[name="ollama_cpus"]').value;
                    const memory = document.querySelector('select[name="ollama_memory"]').value;
                    fetch("/hub/admin/users/api", {
                        method: "POST",
                        credentials: "same-origin",
                        headers: Object.assign({"Content-Type": "application/json"}, xsrf ? {"X-XSRFToken": xsrf} : {}),
                        body: JSON.stringify({ action: "ollama_start", cpus: cpus, memory: memory })
                    }).then(function(r) {
                        return r.json().then(function(body) {
                            if (!r.ok) throw new Error(body.error || "Ollama の起動に失敗しました");
                            window.location.href = "/hub/apps/shared-ollama";
                        });
                    }).catch(function(e) {
                        alert(e.message || "Ollama の起動に失敗しました");
                    });
                    return false;
                }
                const inputs = document.querySelectorAll('input[name="_xsrf"]');
                if (inputs.length > 1) {
                    for (let i = 1; i < inputs.length; i++) inputs[i].remove();
                }
            };
        }
    });
    </script>
    """
    )
    static_js = (
        '<script src="/hub/hpc-resource-meter.js?v=7"></script>'
        '<script src="/hub/hpc-app-status.js?v=8"></script>'
    )
    return header_html + static_js + js_code


# 3. データの受け取り
def options_from_form(formdata):
    """フォーム入力をSpawnerのuser_optionsへ変換する。

    Args:
        formdata: JupyterHubが渡す複数値形式のフォーム辞書。

    Returns:
        検証・正規化済みのuser_options。
    """
    h = formdata.get("hours", ["8"])[0]
    runtime, runtime_line = _hpc_runtime_from_hours_choice(h)
    gpu_max = HPC_GPU_COUNT
    try:
        g = int(formdata.get("gpu", ["0"])[0] or 0)
    except ValueError:
        g = 0
    g = max(0, min(gpu_max, g))
    app_choice = str(formdata.get("app_choice", ["ubuntu-cli"])[0])
    return {
        "nprocs": str(formdata.get("cpu", ["2"])[0]),
        "memory": f"{formdata.get('mem', ['4'])[0]}G",
        "runtime": runtime,
        "runtime_line": runtime_line,
        "gres_line": f"#SBATCH --gres=gpu:{g}" if g > 0 else "",
        "gpu": str(g),
        "app_choice": app_choice,
        "job_name": "jhub-openwebui" if app_choice == "open-webui" else "jhub-app",
        "openwebui_version": HPC_OPENWEBUI_VERSION if app_choice == "open-webui" else "",
        "ubuntu_version": HPC_JUPYTER_UBUNTU_VERSION if app_choice != "open-webui" else "",
    }


def apply_user_options(spawner, user_options):
    """user_optionsをSpawnerの要求リソースと起動設定へ反映する。

    Args:
        spawner: 設定対象のSpawner。
        user_options: options_from_formが生成した設定辞書。
    """
    spawner.req_nprocs = str(user_options.get("nprocs", "2"))
    spawner.req_memory = str(user_options.get("memory", "4G"))
    spawner.req_runtime = str(user_options.get("runtime", "08:00:00"))
    spawner.user_options["nprocs"] = str(user_options.get("nprocs", "2"))
    spawner.user_options["memory"] = str(user_options.get("memory", "4G"))
    spawner.user_options["runtime"] = str(user_options.get("runtime", "08:00:00"))
    spawner.user_options["gpu"] = str(user_options.get("gpu", "0"))
    app_choice = str(user_options.get("app_choice", "ubuntu-cli"))
    spawner.user_options["app_choice"] = app_choice
    spawner.user_options["job_name"] = "jhub-openwebui" if app_choice == "open-webui" else "jhub-app"
    if app_choice == "open-webui":
        spawner.user_options["openwebui_version"] = str(
            user_options.get("openwebui_version", HPC_OPENWEBUI_VERSION)
        )
        # BatchSpawner の service_url が :0 になると pending から進めないため、
        # server_name ベースで Hub 側ポートを事前確定する
        seed = str(getattr(spawner, "name", "") or "")
        h = sum(ord(c) for c in seed) % 20000
        spawner.port = 20000 + h
    else:
        spawner.user_options["ubuntu_version"] = str(
            user_options.get("ubuntu_version", HPC_JUPYTER_UBUNTU_VERSION)
        )
