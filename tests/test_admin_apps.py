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

