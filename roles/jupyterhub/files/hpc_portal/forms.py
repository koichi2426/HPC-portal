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
    gpu_available = resource["gpu_available"]
    gpu_available_count = resource["gpu_available_count"]
    gpu_max = resource["gpu_max"]
    gpu_vram_available_gb = resource["gpu_vram_available_gb"]
    gpu_vram_total_gb = resource["gpu_vram_total_gb"]
    gpu_status = resource["gpu_status"]

    active_sessions_html = ""
    user = spawner.user
    if _hpc_is_portal_admin(user):
        shared = _hpc_shared_ollama_detail_context()
        if shared.get("active"):
            active_sessions_html += (
                f'<div class="gx10-app-card" style="padding:12px;margin-top:10px;">'
                f'<div class="hpc-row-between">'
                f'<div class="hpc-section-title">● Ollama <span class="hpc-muted" style="font-size:11px;">(job {html.escape(str(shared.get("job_id") or ""))})</span></div>'
                f'<div class="hpc-inline-actions">'
                f'<a class="hpc-page-link" href="/hub/apps/shared-ollama">詳細 →</a>'
                f'</div></div>'
                f'<span class="hpc-muted" style="display:block;margin-top:6px;font-size:0.75rem;">割り当て: '
                f'{html.escape(str(shared["allocation"]["cpu"]))} vCPU · {html.escape(str(shared["allocation"]["memory"]))} RAM · 1 GPU · {html.escape(str(shared["allocation"]["hours"]))}</span>'
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

        alloc_html = _hpc_allocation_html(getattr(s, "user_options", None) or {})
        stop_btn = _hpc_stop_button_html(name)

        if getattr(s, "pending", None):
            pending_state = str(getattr(s, "pending", "spawn"))
            active_sessions_html += (
                f'<div class="gx10-app-card" style="padding:12px;margin-top:10px;">'
                f'<div class="hpc-row-between">'
                f'<div>● {app_label} <span class="hpc-status-warn">起動中...</span></div>'
                f'<div>{stop_btn}</div></div>'
                f'{alloc_html}'
                f'<div class="hpc-muted" style="margin-top:8px;font-size:0.78rem;">status: {pending_state}</div>'
                f'<div class="hpc-progress"><div class="hpc-progress-fill"></div></div>'
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
                f'{alloc_html}</div>'
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
            <div class="hpc-muted" style="font-size:0.8rem;">Node: gx10-ac12 <span data-resource-updated-at style="float:right;">5秒ごとに自動更新</span></div>
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
                <div class="resource-meter">
                    <div class="meter-head"><span>RAM 空き</span><span class="meter-status" data-resource-text="mem_status">{mem_status}</span></div>
                    <div class="meter-track" title="RAM 空きリソース">
                        <div class="meter-fill" data-resource-width="mem_available" style="width:{mem_available:.0f}%;"></div>
                    </div>
                    <div class="meter-numbers">
                        <span data-resource-text="mem_available_gb">残り {mem_available_gb:.1f} GB</span>
                        <span data-resource-text="mem_total_gb">最大 {mem_total_gb:.1f} GB</span>
                    </div>
                </div>
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
                <div class="resource-meter">
                    <div class="meter-head"><span>GPU 空き</span><span class="meter-status" data-resource-text="gpu_status">{gpu_status}</span></div>
                    <div class="meter-track" title="GPU VRAM 空きリソース">
                        <div class="meter-fill" data-resource-width="gpu_available" style="width:{gpu_available:.0f}%;"></div>
                    </div>
                    <div class="meter-numbers">
                        <span data-resource-text="gpu_available_count">空き {gpu_available_count}/{gpu_max} GPU</span>
                        <span data-resource-text="gpu_vram_available_gb">VRAM {gpu_vram_available_gb:.1f}/{gpu_vram_total_gb:.1f} GB</span>
                    </div>
                </div>
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
    static_js = '<script src="/hub/hpc-resource-meter.js?v=2"></script>'
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
        # BatchSpawner の service_url が :0 になると pending から進めないため、
        # server_name ベースで Hub 側ポートを事前確定する
        seed = str(getattr(spawner, "name", "") or "")
        h = sum(ord(c) for c in seed) % 20000
        spawner.port = 20000 + h

