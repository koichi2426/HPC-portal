"""アプリ起動フォームの推奨リソースと初期値を検証する。"""

from pathlib import Path
from types import SimpleNamespace

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
    assert recommendations["shared-ollama"]["memory"] == "32G"
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
        "mem_status": "余裕あり",
        "disk_available": 60.0,
        "disk_available_gb": 600.0,
        "disk_total_gb": 1000.0,
        "disk_status": "余裕あり",
        "gpu_max": 1,
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
    assert user_options["memory"] == "32G"
    assert user_options["gpu"] == "1"


def test_spawn_script_applies_selected_recommendation_to_form_values():
    """アプリ変更時に入力欄と案内カードを更新することを確認する。"""
    script = (
        REPOSITORY_ROOT / "roles/jupyterhub/files/hpc-portal-js/spawn-form.js"
    ).read_text()

    assert "function applyRecommendation(option, isSharedOllama)" in script
    assert 'setFormValue("cpu", recommendation.cpu)' in script
    assert 'setFormValue("ollama_cpus", recommendation.cpu)' in script
    assert '"[data-recommendation-summary]": recommendation.summary' in script
