"""アプリ起動フォームの推奨リソースと初期値を検証する。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from tornado import web

from hpc_portal import forms


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_app_resource_recommendations_match_supported_workloads():
    """アプリごとの推奨値が想定する用途に一致することを確認する。"""
    recommendations = forms._hpc_app_resource_recommendations()

    assert recommendations["ubuntu-cli"]["label"] == "JupyterLab"
    assert recommendations["ubuntu-cli"]["cpu"] == "2"
    assert recommendations["ubuntu-cli"]["memory"] == "4"
    assert recommendations["open-webui"]["cpu"] == "2"
    assert recommendations["open-webui"]["memory"] == "4"
    assert recommendations["shared-ollama"]["cpu"] == "8"
    assert recommendations["shared-ollama"]["memory"] == "40G"
    assert recommendations["shared-ollama"]["gpu"] == "1"


def test_app_option_contains_recommendation_data_attributes():
    """選択肢へJavaScriptが利用する推奨値を埋め込むことを確認する。"""
    recommendation = forms._hpc_app_resource_recommendations()["open-webui"]

    option = forms._hpc_app_option_html("open-webui", recommendation)

    assert 'value="open-webui"' in option
    assert 'data-cpu="2"' in option
    assert 'data-memory="4"' in option
    assert 'data-hours="2"' in option
    assert "Open WebUI (AI Chat)" in option


def test_spawn_form_renders_recommendation_card(monkeypatch):
    """起動画面へ推奨値付き選択肢と案内カードを描画することを確認する。"""
    resource = {
        "cpu_available": 50.0,
        "cpu_available_count": 10.0,
        "cpu_total": 20,
        "cpu_status": "余裕あり",
        "mem_available": 75.0,
        "mem_available_gb": 90.0,
        "mem_total_gb": 120.0,
        "mem_used_gb": 30.0,
        "mem_gpu_used_gb": 24.5,
        "mem_status": "余裕あり",
        "mem_slurm_available": 46.0,
        "mem_slurm_available_gb": 55.6,
        "mem_slurm_used_gb": 64.0,
        "mem_slurm_total_gb": 119.6,
        "mem_slurm_status": "やや混雑",
        "disk_available": 60.0,
        "disk_available_gb": 600.0,
        "disk_total_gb": 1000.0,
        "disk_status": "余裕あり",
        "gpu_max": 1,
        "gpu_available": 0.0,
        "gpu_available_count": 0,
        "gpu_status": "逼迫",
        "gpu_processes": [],
        "gpu_processes_available": True,
    }
    user = SimpleNamespace(name="user01", spawners={})
    spawner = SimpleNamespace(
        user=user,
        notebook_dir="/home/user01",
        homedir="/home/user01",
    )
    monkeypatch.setattr(forms, "_hpc_resource_snapshot", lambda _path: resource)
    monkeypatch.setattr(forms, "_hpc_is_portal_admin", lambda _user: False)

    rendered = forms.make_options_form(spawner)

    assert 'id="app-resource-recommendation"' in rendered
    assert 'data-label="JupyterLab"' in rendered
    assert 'data-label="Open WebUI"' in rendered
    assert 'data-label="Ollama"' not in rendered
    assert "データ分析は4 vCPU・8 GB" in rendered
    # GPUメーターが空き枚数を示すこと（利用者が起動可否を判断できる）
    assert "残り 0 / 1 枚" in rendered
    assert 'data-resource-width="gpu_available"' in rendered
    # 統合メモリのメーターはSlurmの割当可能量を示し、OS実空きは併記に留めること
    assert 'data-resource-width="mem_slurm_available"' in rendered
    assert "残り 55.6 GB" in rendered
    assert 'data-resource-width="mem_available"' not in rendered


def test_missing_form_values_fall_back_to_selected_app_recommendation():
    """入力が欠けても選択アプリの推奨値を使用することを確認する。"""
    user_options = forms.options_from_form({"app_choice": ["open-webui"]})

    assert user_options["nprocs"] == "2"
    assert user_options["memory"] == "4G"
    assert user_options["gpu"] == "0"
    assert user_options["runtime"] == "02:00:00"


def test_shared_ollama_memory_default_keeps_single_unit_suffix():
    """Ollama推奨メモリの単位を重複させないことを確認する。"""
    user_options = forms.options_from_form({"app_choice": ["shared-ollama"]})

    assert user_options["nprocs"] == "8"
    assert user_options["memory"] == "40G"
    assert user_options["gpu"] == "1"


def test_spawn_form_renders_shared_ollama_runtime_settings(monkeypatch):
    """管理者の起動画面へ共有Ollamaの初期設定を描画する。"""
    resource = {
        "cpu_available": 50.0,
        "cpu_available_count": 10.0,
        "cpu_total": 20,
        "cpu_status": "余裕あり",
        "mem_available": 75.0,
        "mem_available_gb": 90.0,
        "mem_total_gb": 120.0,
        "mem_used_gb": 30.0,
        "mem_gpu_used_gb": 24.5,
        "mem_status": "余裕あり",
        "mem_slurm_available": 46.0,
        "mem_slurm_available_gb": 55.6,
        "mem_slurm_used_gb": 64.0,
        "mem_slurm_total_gb": 119.6,
        "mem_slurm_status": "やや混雑",
        "disk_available": 60.0,
        "disk_available_gb": 600.0,
        "disk_total_gb": 1000.0,
        "disk_status": "余裕あり",
        "gpu_max": 1,
        "gpu_available": 0.0,
        "gpu_available_count": 0,
        "gpu_status": "逼迫",
        "gpu_processes": [],
        "gpu_processes_available": True,
    }
    user = SimpleNamespace(name="admin", spawners={})
    spawner = SimpleNamespace(
        user=user, notebook_dir="/home/admin", homedir="/home/admin"
    )
    monkeypatch.setattr(forms, "_hpc_resource_snapshot", lambda _path: resource)
    monkeypatch.setattr(forms, "_hpc_is_portal_admin", lambda _user: True)
    monkeypatch.setattr(
        forms,
        "_hpc_shared_ollama_detail_context",
        lambda: {"active": False},
    )

    rendered = forms.make_options_form(spawner)

    assert 'name="ollama_memory"' in rendered
    assert '<option value="40G" selected>40G RAM</option>' in rendered
    assert 'name="ollama_parallel"' in rendered
    assert 'name="ollama_context_length"' in rendered
    assert '<option value="131072" selected>128K</option>' in rendered
    assert '<option value="q8_0" selected>q8_0</option>' in rendered


def test_spawn_script_applies_selected_recommendation_to_form_values():
    """アプリ変更時に入力欄と案内カードを更新することを確認する。"""
    script = (
        REPOSITORY_ROOT / "roles/jupyterhub/files/hpc-portal-js/spawn-form.js"
    ).read_text()

    assert "function applyRecommendation(option, isSharedOllama)" in script
    assert 'setFormValue("cpu", recommendation.cpu)' in script
    assert 'setFormValue("ollama_cpus", recommendation.cpu)' in script
    assert '"[data-recommendation-summary]": recommendation.summary' in script


def _resource_meter_sources():
    """リソースメーターを描画しているファイルを列挙する。"""
    root = Path(__file__).resolve().parents[1] / "roles/jupyterhub"
    candidates = list((root / "templates").glob("*.j2"))
    candidates.append(root / "files/hpc_portal/forms.py")
    return [
        path
        for path in candidates
        if "data-resource-width" in path.read_text(encoding="utf-8")
    ]


def test_every_resource_meter_uses_slurm_backed_values():
    """メーターを持つ全ての描画元がSlurm割当ベースの値を使うことを確認する。

    ホーム画面・アプリ詳細・起動フォームがそれぞれ同じマークアップを複製して
    いるため、片方だけ直すとOS実空きを表示したままになる。
    """
    sources = _resource_meter_sources()

    assert len(sources) >= 3, f"描画元の検出漏れ: {[p.name for p in sources]}"
    for path in sources:
        body = path.read_text(encoding="utf-8")
        assert 'data-resource-width="mem_available"' not in body, (
            f"{path.name} がOS実空きをメーターに使っている"
        )
        assert 'data-resource-width="mem_slurm_available"' in body, path.name
        assert 'data-resource-width="gpu_available"' in body, path.name


def test_every_resource_meter_shows_gpu_share_of_unified_memory():
    """統合メモリの内訳にGPU確保分を出す描画元が揃っていることを確認する。

    GB10ではGPUの確保分がOS実使用に現れないため、内訳が無い画面は実態の
    1/10ほどしか示さない。3箇所が同じマークアップを複製しているので、
    片方だけ直しても気付けない。
    """
    sources = _resource_meter_sources()

    assert len(sources) >= 3, f"描画元の検出漏れ: {[p.name for p in sources]}"
    for path in sources:
        body = path.read_text(encoding="utf-8")
        assert 'data-resource-text="mem_gpu_used_gb"' in body, (
            f"{path.name} が統合メモリのGPU内訳を表示していない"
        )


def test_resource_meter_script_formats_gpu_share():
    """定期更新のJavaScript側もGPU内訳を描き替えることを確認する。"""
    script = (
        REPOSITORY_ROOT / "roles/jupyterhub/files/hpc-portal-js/resource-meter.js"
    ).read_text(encoding="utf-8")

    assert "mem_gpu_used_gb: format1(data.mem_gpu_used_gb)" in script


@pytest.mark.parametrize(
    ("raw", "gigabytes"),
    [("40G", 40.0), ("4096M", 4.0), ("1T", 1024.0), ("8", 8.0), ("", None), ("bad", None)],
)
def test_parse_requested_memory_gb(raw, gigabytes):
    assert forms._hpc_parse_requested_memory_gb(raw) == gigabytes


def _free(cpu=12.0, mem_mb=56970, gpu=1):
    return {
        "cpu_total": 20,
        "cpu_available_count": cpu,
        "mem_total_mb": 122506,
        "mem_available_mb": mem_mb,
        "gpu_max": 1,
        "gpu_available_count": gpu,
    }


def test_requested_resources_pass_when_within_free_capacity(monkeypatch):
    monkeypatch.setattr(forms, "_hpc_slurm_free_resources", lambda: _free())

    assert forms._hpc_requested_resources_error("8", "32G") == ""


def test_requested_resources_reject_memory_over_capacity(monkeypatch):
    """空き55.6GBに対する96G要求は、5分待たずに理由付きで弾く。"""
    monkeypatch.setattr(forms, "_hpc_slurm_free_resources", lambda: _free())

    error = forms._hpc_requested_resources_error("4", "96G")

    assert "メモリ" in error
    assert "96" in error and "55.6" in error


def test_requested_resources_ignore_gpu_availability(monkeypatch):
    """GPUはGRES予約せず共有するため、空き枚数では弾かない。

    枚数で弾くと、実際には起動できる構成まで拒否してしまう。
    GPUの確保分は統合メモリから出ていくため、メモリ判定が実質的な歯止めになる。
    """
    monkeypatch.setattr(forms, "_hpc_slurm_free_resources", lambda: _free(gpu=0))

    assert forms._hpc_requested_resources_error("2", "4G") == ""


def test_requested_resources_reject_cpu_over_capacity(monkeypatch):
    monkeypatch.setattr(forms, "_hpc_slurm_free_resources", lambda: _free(cpu=4.0))

    error = forms._hpc_requested_resources_error("16", "4G")

    assert "vCPU" in error


def test_requested_resources_allow_when_slurm_unavailable(monkeypatch):
    """Slurmへ問い合わせられないときは判断材料が無いため通す。"""
    monkeypatch.setattr(forms, "_hpc_slurm_free_resources", lambda: None)

    assert forms._hpc_requested_resources_error("20", "999G") == ""


def test_options_from_form_rejects_request_over_capacity(monkeypatch):
    """sbatchへ渡す前に400で弾き、PENDINGの5分待ちを避ける。"""
    monkeypatch.setattr(forms, "_hpc_slurm_free_resources", lambda: _free())

    with pytest.raises(web.HTTPError) as excinfo:
        forms.options_from_form({"app_choice": ["ubuntu-cli"], "mem": ["96"], "cpu": ["2"]})

    assert excinfo.value.status_code == 400
    assert "メモリ" in str(excinfo.value.log_message)


def test_user_jobs_no_longer_reserve_gpu_gres():
    """利用者ジョブはGRES予約しない。

    ノードのGPUは1枚しかなく、予約すると2人目以降が永久にPENDINGになる。
    起動スクリプトは常に apptainer exec --nv で実行するため、予約が無くてもGPUは使える。
    """
    user_options = forms.options_from_form(
        {"app_choice": ["ubuntu-cli"], "gpu": ["1"], "mem": ["16"]}
    )

    assert user_options["gres_line"] == ""
    assert user_options["gpu"] == "1"
    # 「使う」を選んだジョブはGPUを隠さない
    assert user_options["gpu_visibility_line"] == ""


def test_jobs_without_gpu_hide_cuda_devices():
    """「使わない」を選んだジョブはCUDAからGPUを隠す。

    GRES予約をやめた以上スケジューラ側では締め出せず、統合メモリのため
    意図しないGPU確保がそのまま要求メモリの枠を圧迫する。
    """
    user_options = forms.options_from_form(
        {"app_choice": ["ubuntu-cli"], "gpu": ["0"], "mem": ["4"]}
    )

    assert user_options["gres_line"] == ""
    assert user_options["gpu_visibility_line"] == 'export CUDA_VISIBLE_DEVICES=""'


def test_batch_script_applies_gpu_visibility():
    """起動スクリプトがGPUの可視性設定を展開することを確認する。"""
    script = (
        REPOSITORY_ROOT / "roles/jupyterhub/files/hpc_portal/batch.py"
    ).read_text(encoding="utf-8")

    assert "gpu_visibility_line" in script
    # --nv は常に付く（GPUの可視性は CUDA_VISIBLE_DEVICES だけで決まる）
    assert script.count("apptainer exec --nv") >= 2


def test_spawn_form_offers_shared_gpu_choice(monkeypatch):
    """GPU欄が枚数ではなく共有の可否になっていることを確認する。"""
    resource = {
        "cpu_available": 50.0, "cpu_available_count": 10.0, "cpu_total": 20,
        "cpu_status": "余裕あり", "mem_available": 75.0, "mem_available_gb": 90.0,
        "mem_total_gb": 120.0, "mem_used_gb": 30.0, "mem_gpu_used_gb": 24.5,
        "mem_status": "余裕あり", "mem_slurm_available": 46.0,
        "mem_slurm_available_gb": 55.6, "mem_slurm_used_gb": 64.0,
        "mem_slurm_total_gb": 119.6, "mem_slurm_status": "やや混雑",
        "disk_available": 60.0, "disk_available_gb": 600.0, "disk_total_gb": 1000.0,
        "disk_status": "余裕あり", "gpu_max": 1, "gpu_available": 100.0,
        "gpu_available_count": 1, "gpu_status": "余裕あり",
        "gpu_processes": [], "gpu_processes_available": True,
    }
    user = SimpleNamespace(name="user01", spawners={})
    spawner = SimpleNamespace(
        user=user, notebook_dir="/home/user01", homedir="/home/user01"
    )
    monkeypatch.setattr(forms, "_hpc_resource_snapshot", lambda _path: resource)
    monkeypatch.setattr(forms, "_hpc_is_portal_admin", lambda _user: False)

    rendered = forms.make_options_form(spawner)

    assert '<option value="1">使う（全員で共有）</option>' in rendered
    assert 'name="gpu"' in rendered
    # 枚数入力は残さない（1枚を取り合う形ではなくなったため）
    assert 'type="number" class="form-control input-dark" name="gpu"' not in rendered
    # GPU確保分もメモリ枠から出ることを画面で伝える
    assert "GPUが確保した分も上のRAMから消費されます" in rendered


def test_spawn_script_raises_memory_when_gpu_selected():
    """GPUを選んだときに推奨メモリを引き上げることを確認する。

    既定の4GBのままGPUを使うと、モデルの確保分で要求を大きく超える。
    弾かずに既定値で誘導する（意図的に小さくする利用者は下げられる）。
    """
    script = (
        REPOSITORY_ROOT / "roles/jupyterhub/files/hpc-portal-js/spawn-form.js"
    ).read_text(encoding="utf-8")

    assert "GPU_RECOMMENDED_MEMORY_GB = 16" in script
    assert "function applyGpuMemoryFloor()" in script


def test_spawn_script_announces_memory_change():
    """メモリを自動変更したことを利用者へ示すことを確認する。

    利用者が自分で入れた値を黙って書き換えると「あれ？」となるため、
    変更前後の値と、変更できることを伝える。
    """
    script = (
        REPOSITORY_ROOT / "roles/jupyterhub/files/hpc-portal-js/spawn-form.js"
    ).read_text(encoding="utf-8")

    assert "function announceMemoryBump(" in script
    assert "hpc-field-bumped" in script
    assert "data-mem-bump-hint" in script


def test_spawn_form_has_memory_hint_slot(monkeypatch):
    """メモリ欄に変更通知の表示枠があることを確認する。"""
    resource = {
        "cpu_available": 50.0, "cpu_available_count": 10.0, "cpu_total": 20,
        "cpu_status": "余裕あり", "mem_available": 75.0, "mem_available_gb": 90.0,
        "mem_total_gb": 120.0, "mem_used_gb": 30.0, "mem_gpu_used_gb": 24.5,
        "mem_status": "余裕あり", "mem_slurm_available": 46.0,
        "mem_slurm_available_gb": 55.6, "mem_slurm_used_gb": 64.0,
        "mem_slurm_total_gb": 119.6, "mem_slurm_status": "やや混雑",
        "disk_available": 60.0, "disk_available_gb": 600.0, "disk_total_gb": 1000.0,
        "disk_status": "余裕あり", "gpu_max": 1, "gpu_available": 100.0,
        "gpu_available_count": 1, "gpu_status": "余裕あり",
        "gpu_processes": [], "gpu_processes_available": True,
    }
    user = SimpleNamespace(name="user01", spawners={})
    spawner = SimpleNamespace(
        user=user, notebook_dir="/home/user01", homedir="/home/user01"
    )
    monkeypatch.setattr(forms, "_hpc_resource_snapshot", lambda _path: resource)
    monkeypatch.setattr(forms, "_hpc_is_portal_admin", lambda _user: False)

    rendered = forms.make_options_form(spawner)

    assert "data-mem-bump-hint" in rendered
    assert 'aria-live="polite"' in rendered


def test_form_styles_respect_reduced_motion():
    """アニメーションが reduced-motion 設定を尊重することを確認する。"""
    css = (
        REPOSITORY_ROOT / "roles/jupyterhub/files/hpc-portal-css/60-app-forms.css"
    ).read_text(encoding="utf-8")

    assert "@keyframes hpc-field-bump" in css
    assert "prefers-reduced-motion" in css
