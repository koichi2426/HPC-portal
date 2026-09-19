"""管理者向けSlurmアプリ一覧の解析、失敗処理、キャッシュを検証する。"""

from types import SimpleNamespace

import pytest

from hpc_portal.handlers import admin_apps


@pytest.mark.parametrize(
    ("raw", "expected"), [("", None), ("N/A", None), ("512", 512), ("1K", 1024), ("1.5M", 1572864), ("2G", 2 * 1024**3), ("-2M", 0), ("bad", None)]
)
def test_slurm_memory_bytes(raw, expected):
    assert admin_apps._hpc_slurm_memory_bytes(raw) == expected


def test_slurm_max_rss_parses_maximum_and_ignores_unrequested_jobs(monkeypatch):
    admin_apps._HPC_ADMIN_APPS_RSS_CACHE.update(expires_at=0.0, job_ids=(), usage={})
    stdout = "42.batch|512M|\n42.extern|1G|\n99.batch|8G|\ninvalid\n"
    monkeypatch.setattr(
        admin_apps,
        "_hpc_run_cmd",
        lambda command, timeout: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )

    assert admin_apps._hpc_slurm_max_rss(["42"]) == {"42": 1024**3}


def test_admin_apps_snapshot_parses_only_portal_jobs(monkeypatch):
    stdout = "\n".join(
        [
            "42|user01|jhub-app|RUNNING|2|4G|N/A|01:00|2026-01-01T00:00:00",
            "43|user02|jhub-openwebui|PENDING|4|8G|gpu:1|00:00|N/A",
            "44|hpc-ollama|shared-ollama|RUNNING|8|32G|gpu:a100:1|10:00|2026-01-01T00:00:00",
            "45|user01|unrelated|RUNNING|1|1G|N/A|00:01|2026-01-01T00:00:00",
            "malformed",
        ]
    )
    monkeypatch.setattr(
        admin_apps,
        "_hpc_run_cmd",
        lambda command, timeout: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )
    monkeypatch.setattr(
        admin_apps,
        "_hpc_linux_users_snapshot",
        lambda: [
            {"username": "user01", "display_name": "利用者一"},
            {"username": "user02", "display_name": "利用者二"},
        ],
    )
    monkeypatch.setattr(admin_apps, "_hpc_slurm_max_rss", lambda job_ids: {"42": 1024})

    rows, error = admin_apps._hpc_admin_apps_snapshot_uncached()

    assert error == ""
    assert [row["job_id"] for row in rows] == ["44", "42", "43"]
    by_id = {row["job_id"]: row for row in rows}
    assert by_id["42"]["app"] == "JupyterLab"
    assert by_id["42"]["max_rss_label"] == "1.0 KB"
    assert by_id["43"]["gpus"] == 1
    assert by_id["43"]["state_label"] == "実行待ち"
    assert by_id["43"]["max_rss_label"] == "計測待ち"
    assert by_id["44"]["display_name"] == "共有"
    assert by_id["44"]["gpus"] == 1


def test_admin_apps_snapshot_bounds_command_error(monkeypatch):
    monkeypatch.setattr(
        admin_apps,
        "_hpc_run_cmd",
        lambda command, timeout: SimpleNamespace(returncode=1, stdout="", stderr="x" * 500),
    )

    rows, error = admin_apps._hpc_admin_apps_snapshot_uncached()

    assert rows == []
    assert len(error) == 300


def test_admin_apps_snapshot_reports_timeout(monkeypatch):
    monkeypatch.setattr(
        admin_apps,
        "_hpc_run_cmd",
        lambda command, timeout: (_ for _ in ()).throw(
            admin_apps.subprocess.TimeoutExpired(command, timeout)
        ),
    )

    rows, error = admin_apps._hpc_admin_apps_snapshot_uncached()

    assert rows == []
    assert "タイムアウト" in error


def test_admin_apps_cache_returns_copy_without_refetch(monkeypatch):
    admin_apps._HPC_ADMIN_APPS_CACHE.update(expires_at=0.0, apps=[], error="")
    calls = []
    monkeypatch.setattr(
        admin_apps,
        "_hpc_admin_apps_snapshot_uncached",
        lambda: calls.append(True) or ([{"job_id": "42"}], ""),
    )

    first, _ = admin_apps._hpc_admin_apps_snapshot()
    first[0]["job_id"] = "changed"
    second, _ = admin_apps._hpc_admin_apps_snapshot()

    assert len(calls) == 1
    assert second == [{"job_id": "42"}]



def test_job_gpu_memory_sums_processes_per_job(monkeypatch):
    """nvidia-smiのPIDをcgroup経由でSlurmジョブへ紐付けて集計する。"""
    monkeypatch.setattr(
        admin_apps,
        "_hpc_run_cmd",
        lambda command, timeout: SimpleNamespace(
            returncode=0,
            stdout="100, 25053\n101, 1024\n102, 512\nmalformed\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        admin_apps,
        "_hpc_job_id_of_pid",
        lambda pid: {100: "44", 101: "44", 102: ""}.get(pid, ""),
    )

    usage = admin_apps._hpc_job_gpu_memory_bytes()

    # ジョブに属さないPID(102)は除外し、同一ジョブのPIDは合算する
    assert usage == {"44": (25053 + 1024) * 1024**2}


def test_job_gpu_memory_returns_empty_on_command_failure(monkeypatch):
    monkeypatch.setattr(
        admin_apps,
        "_hpc_run_cmd",
        lambda command, timeout: SimpleNamespace(returncode=1, stdout="", stderr="no gpu"),
    )

    assert admin_apps._hpc_job_gpu_memory_bytes() == {}


def test_job_id_of_pid_reads_slurm_cgroup(tmp_path, monkeypatch):
    """/proc/<pid>/cgroup の job_<ID> からジョブを特定する。"""
    proc_dir = tmp_path / "1234"
    proc_dir.mkdir()
    (proc_dir / "cgroup").write_text(
        "0::/system.slice/gx10-ac12_slurmstepd.scope/job_12/step_batch/user/task_0\n",
        encoding="utf-8",
    )
    real_open = admin_apps.open if hasattr(admin_apps, "open") else open
    monkeypatch.setattr(
        "builtins.open",
        lambda path, *args, **kwargs: real_open(
            str(proc_dir / "cgroup") if str(path).startswith("/proc/") else path,
            *args,
            **kwargs,
        ),
    )

    assert admin_apps._hpc_job_id_of_pid(1234) == "12"


def test_job_id_of_pid_returns_empty_when_unreadable(monkeypatch):
    def raise_oserror(path, *args, **kwargs):
        raise OSError("no such process")

    monkeypatch.setattr("builtins.open", raise_oserror)

    assert admin_apps._hpc_job_id_of_pid(999999) == ""


def test_admin_apps_snapshot_adds_gpu_memory_to_used_total(monkeypatch):
    """統合メモリではGPU確保分がMaxRSSに現れないため、合算して表示する。"""
    stdout = "44|hpc-ollama|shared-ollama|RUNNING|8|40G|N/A|10:00|2026-01-01T00:00:00"
    monkeypatch.setattr(
        admin_apps,
        "_hpc_run_cmd",
        lambda command, timeout: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )
    monkeypatch.setattr(admin_apps, "_hpc_linux_users_snapshot", lambda: [])
    monkeypatch.setattr(
        admin_apps, "_hpc_slurm_max_rss", lambda job_ids: {"44": 2 * 1024**3}
    )
    monkeypatch.setattr(
        admin_apps, "_hpc_job_gpu_memory_bytes", lambda: {"44": 24 * 1024**3}
    )

    rows, error = admin_apps._hpc_admin_apps_snapshot_uncached()

    assert error == ""
    row = rows[0]
    assert row["max_rss_bytes"] == 2 * 1024**3
    assert row["gpu_memory_bytes"] == 24 * 1024**3
    assert row["memory_used_bytes"] == 26 * 1024**3
    assert row["memory_used_label"] == "26.0 GB"


def test_admin_apps_snapshot_keeps_label_when_nothing_measurable(monkeypatch):
    """CPU側もGPU側も取得できない場合は従来どおりの文言を保つ。"""
    stdout = "43|user02|jhub-openwebui|PENDING|4|8G|N/A|00:00|N/A"
    monkeypatch.setattr(
        admin_apps,
        "_hpc_run_cmd",
        lambda command, timeout: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )
    monkeypatch.setattr(admin_apps, "_hpc_linux_users_snapshot", lambda: [])
    monkeypatch.setattr(admin_apps, "_hpc_slurm_max_rss", lambda job_ids: {})
    monkeypatch.setattr(admin_apps, "_hpc_job_gpu_memory_bytes", lambda: {})

    rows, _ = admin_apps._hpc_admin_apps_snapshot_uncached()

    assert rows[0]["memory_used_bytes"] is None
    assert rows[0]["memory_used_label"] == "計測待ち"
    assert rows[0]["gpu_memory_label"] == "—"


@pytest.mark.parametrize(
    ("used", "requested", "level"),
    [
        (8 * 1024**3, "16G", ""),            # 要求内
        (16 * 1024**3, "16G", ""),           # ちょうど
        (18 * 1024**3, "16G", "caution"),    # 超過だが2割以内
        (26 * 1024**3, "16G", "warning"),    # 2割超
        (None, "16G", ""),                   # 実使用を取得できない
        (8 * 1024**3, "", ""),               # 要求を解釈できない
    ],
)
def test_memory_overuse_levels(used, requested, level):
    result = admin_apps._hpc_memory_overuse(used, requested)

    assert result["memory_overuse_level"] == level
    if level:
        assert "要求" in result["memory_overuse_label"]
    else:
        assert result["memory_overuse_label"] == ""


def test_memory_overuse_reports_concrete_amounts():
    """割合だけでなく実使用量と超過量を実数で示す。

    「何%超過」だけでは、次にどれだけ要求を増やせばよいか分からない。
    """
    result = admin_apps._hpc_memory_overuse(32 * 1024**3, "8G")

    assert result["memory_limit_bytes"] == 8 * 1024**3
    assert result["memory_usage_ratio"] == 4.0
    label = result["memory_overuse_label"]
    assert "8.0 GB" in label      # 要求
    assert "32.0 GB" in label     # 実使用
    assert "24.0 GB 超過" in label  # 差分
    assert "400%" in label


def test_admin_apps_snapshot_flags_memory_overuse(monkeypatch):
    """GPU確保分を含めた実使用が要求を超えたジョブへ警告を付ける。

    ConstrainRAMSpace=no のため超過しても停止しない。統合メモリでは
    GPU確保分がcgroupにも現れないため、表示だけが気付く手段になる。
    """
    stdout = "44|user01|jhub-app|RUNNING|2|8G|N/A|10:00|2026-01-01T00:00:00"
    monkeypatch.setattr(
        admin_apps,
        "_hpc_run_cmd",
        lambda command, timeout: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )
    monkeypatch.setattr(admin_apps, "_hpc_linux_users_snapshot", lambda: [])
    monkeypatch.setattr(
        admin_apps, "_hpc_slurm_max_rss", lambda job_ids: {"44": 2 * 1024**3}
    )
    monkeypatch.setattr(
        admin_apps, "_hpc_job_gpu_memory_bytes", lambda: {"44": 24 * 1024**3}
    )

    rows, _ = admin_apps._hpc_admin_apps_snapshot_uncached()

    row = rows[0]
    assert row["memory_used_bytes"] == 26 * 1024**3
    assert row["memory_overuse_level"] == "warning"
    assert "8.0 GB" in row["memory_overuse_label"]


def test_admin_apps_js_renders_overuse_warning():
    """管理画面のJavaScriptが超過を描画することを確認する。"""
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[1]
        / "roles/jupyterhub/files/hpc-portal-js/admin-apps.js"
    ).read_text(encoding="utf-8")

    assert "memory_overuse_label" in script
    assert "hpc-memory-overuse-" in script


def test_admin_apps_list_shows_actual_usage_column():
    """一覧の時点で実使用メモリが見えることを確認する。

    詳細を開かないと超過に気付けないと、管理者が一覧を眺めても見落とす。
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[1]
        / "roles/jupyterhub/files/hpc-portal-js/admin-apps.js"
    ).read_text(encoding="utf-8")
    home = (
        Path(__file__).resolve().parents[1]
        / "roles/jupyterhub/templates/home.html.j2"
    ).read_text(encoding="utf-8")

    assert '"実使用メモリ"' in js
    assert "<th>実使用メモリ</th>" in home
    # 列を増やしたので、詳細行と空行のcolspanも揃っていること
    assert "detailCell.colSpan = 9;" in js
    assert "emptyCell.colSpan = 9;" in js
    assert 'colspan="9"' in home
