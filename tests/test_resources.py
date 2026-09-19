"""Slurmとリソース表示で使用する純粋な変換処理を検証する。"""

from types import SimpleNamespace

import pytest

from hpc_portal import resources
from hpc_portal.schemas import HpcResourceSnapshot


@pytest.mark.parametrize(
    ("available", "status"), [(100, "余裕あり"), (50, "余裕あり"), (49.9, "やや混雑"), (25, "やや混雑"), (24.9, "逼迫"), (0, "逼迫")]
)
def test_resource_status_boundary(available, status):
    assert resources._hpc_resource_status(available) == status


@pytest.mark.parametrize(
    ("raw", "megabytes"), [("", 0), ("4G", 4096), ("1.5G", 1536), ("8192M", 8192), ("512", 512)]
)
def test_parse_slurm_memory(raw, megabytes):
    assert resources._parse_slurm_mem_to_mb(raw) == megabytes


def test_parse_slurm_tres_reads_cpu_memory_and_gpu():
    parsed = resources._parse_slurm_tres("cpu=8,mem=32G,gres/gpu=1,billing=8")

    assert parsed == {"cpu": 8, "mem_mb": 32768, "gpu": 1}


def test_slurm_field_map_ignores_tokens_without_equals():
    assert resources._slurm_field_map("NodeName=test State=MIXED invalid CfgTRES=cpu=20,mem=32G") == {
        "NodeName": "test",
        "State": "MIXED",
        "CfgTRES": "cpu=20,mem=32G",
    }


@pytest.mark.parametrize(("raw", "count"), [("gpu:1", 1), ("gpu:tesla:2", 0), ("(null)", 0), ("gpu:x", 0)])
def test_parse_slurm_gres_count(raw, count):
    assert resources._parse_slurm_gres_count(raw) == count


def test_slurm_free_resources_calculates_non_negative_available_values(monkeypatch):
    stdout = "NodeName=test CPUTot=20 CPUAlloc=25 RealMemory=1000 AllocMem=1200 Gres=gpu:1 CfgTRES=cpu=20,mem=1000M,gres/gpu=1 AllocTRES=cpu=25,mem=1200M,gres/gpu=2"
    monkeypatch.setattr(
        resources.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": stdout})(),
    )

    result = resources._hpc_slurm_free_resources()

    assert result["cpu_available_count"] == 0
    assert result["mem_available_mb"] == 0
    assert result["gpu_available_count"] == 0


def test_slurm_free_resources_returns_none_on_command_failure(monkeypatch):
    monkeypatch.setattr(
        resources.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 1, "stdout": ""})(),
    )

    assert resources._hpc_slurm_free_resources() is None



@pytest.mark.parametrize(
    ("raw", "count"),
    [
        ("gres/gpu:1", 1),
        ("gres/gpu:2", 2),
        ("gres/gpu:a100:4", 4),
        ("gres/gpu:1,gres/gpu:2", 3),
        ("N/A", 0),
        ("", 0),
        ("gres/mps:50", 0),
    ],
)
def test_parse_slurm_gpu_tres_per_node(raw, count):
    assert resources._parse_slurm_gpu_tres_per_node(raw) == count


def _fake_slurm_commands(monkeypatch, scontrol_stdout, squeue_stdout, squeue_rc=0):
    """scontrol と squeue の出力を切り替えるダミーを仕込む。"""

    def run(command, *args, **kwargs):
        if command[0] == "squeue":
            return type("Result", (), {"returncode": squeue_rc, "stdout": squeue_stdout})()
        return type("Result", (), {"returncode": 0, "stdout": scontrol_stdout})()

    monkeypatch.setattr(resources.subprocess, "run", run)


def test_slurm_allocated_gpus_sums_running_jobs(monkeypatch):
    _fake_slurm_commands(monkeypatch, "", "gres/gpu:1\nN/A\ngres/gpu:2\n")

    assert resources._hpc_slurm_allocated_gpus() == 3


def test_slurm_allocated_gpus_returns_none_on_command_failure(monkeypatch):
    _fake_slurm_commands(monkeypatch, "", "", squeue_rc=1)

    assert resources._hpc_slurm_allocated_gpus() is None


def test_slurm_free_resources_falls_back_to_squeue_when_alloctres_lacks_gpu(monkeypatch):
    """AccountingStorageTRESにgres/gpuが無い環境でもGPU割当を検出する。"""
    # 実機の出力と同じく AllocTRES に gres/gpu が現れない
    scontrol = (
        "NodeName=test CPUTot=20 CPUAlloc=8 RealMemory=122506 Gres=gpu:1 "
        "CfgTRES=cpu=20,mem=122506M,billing=20 AllocTRES=cpu=8,mem=64G"
    )
    _fake_slurm_commands(monkeypatch, scontrol, "gres/gpu:1\n")

    result = resources._hpc_slurm_free_resources()

    assert result["gpu_max"] == 1
    assert result["gpu_available_count"] == 0


def test_slurm_free_resources_reports_gpu_free_when_nothing_holds_it(monkeypatch):
    scontrol = (
        "NodeName=test CPUTot=20 CPUAlloc=8 RealMemory=122506 Gres=gpu:1 "
        "CfgTRES=cpu=20,mem=122506M,billing=20 AllocTRES=cpu=8,mem=64G"
    )
    _fake_slurm_commands(monkeypatch, scontrol, "N/A\n")

    assert resources._hpc_slurm_free_resources()["gpu_available_count"] == 1


def test_resource_snapshot_exposes_slurm_backed_memory_and_gpu(monkeypatch):
    """メーターがSlurmの割当状況を反映し、Schema検証を通ることを確認する。"""
    monkeypatch.setattr(
        resources,
        "_hpc_slurm_free_resources",
        lambda: {
            "cpu_total": 20,
            "cpu_available_count": 12.0,
            "mem_total_mb": 122506,
            "mem_available_mb": 56970,
            "gpu_max": 1,
            "gpu_available_count": 0,
        },
    )
    monkeypatch.setattr(resources, "_hpc_gpu_process_snapshot", lambda: ([], True))
    monkeypatch.setattr(
        resources.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=128 * 1024 ** 3, available=121 * 1024 ** 3),
    )
    monkeypatch.setattr(
        resources.psutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=1000 * 1024 ** 3, free=500 * 1024 ** 3),
    )

    snapshot = resources._hpc_resource_snapshot()

    # GPUは1枚すべて予約済みなので空きは0
    assert snapshot["gpu_available_count"] == 0
    assert snapshot["gpu_available"] == 0
    assert snapshot["gpu_status"] == "逼迫"
    # メーターはSlurmの割当可能量(55.6GB)を示し、OS実空き(121GB)とは別に保持する
    assert round(snapshot["mem_slurm_available_gb"], 1) == 55.6
    assert round(snapshot["mem_available_gb"], 1) == 121.0
    assert snapshot["mem_slurm_available"] < snapshot["mem_available"]
    # Schema検証を通ること（キー欠落は実行時に例外となるため）
    HpcResourceSnapshot.model_validate(snapshot)


@pytest.mark.parametrize(
    ("raw", "megabytes"),
    [
        ("25053", 25053),
        (" 25053 ", 25053),
        ("25053 MiB", 25053),
        ("[N/A]", None),
        ("", None),
        ("-", None),
    ],
)
def test_parse_nvidia_smi_memory(raw, megabytes):
    assert resources._parse_nvidia_smi_memory_mb(raw) == megabytes


def _fake_nvidia_smi(monkeypatch, stdout, returncode=0):
    """nvidia-smi の compute-apps 問い合わせだけを差し替える。"""

    def run(command, **kwargs):
        assert command[0] == "nvidia-smi"
        assert "--query-compute-apps=pid,process_name,used_memory" in command
        return SimpleNamespace(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(resources.subprocess, "run", run)
    monkeypatch.setattr(
        resources.psutil,
        "Process",
        lambda pid: SimpleNamespace(name=lambda: "ollama", username=lambda: "hpc-ollama"),
    )


def test_gpu_process_snapshot_reads_used_memory(monkeypatch):
    """GB10ではnvidia-smiだけがGPU確保量を返すため、プロセスごとに保持する。"""
    _fake_nvidia_smi(monkeypatch, "2275817, /usr/lib/ollama/llama-server, 25053\n")

    processes, available = resources._hpc_gpu_process_snapshot()

    assert available is True
    assert processes == [
        {
            "pid": 2275817,
            "name": "Ollama",
            "username": "hpc-ollama",
            "memory_mb": 25053,
        }
    ]


def test_gpu_process_snapshot_tolerates_unavailable_memory(monkeypatch):
    """used_memoryが[N/A]でもプロセス一覧そのものは失わない。"""
    _fake_nvidia_smi(monkeypatch, "100, /usr/lib/ollama/llama-server, [N/A]\n")

    processes, available = resources._hpc_gpu_process_snapshot()

    assert available is True
    assert processes[0]["memory_mb"] is None


def test_resource_snapshot_reports_gpu_share_of_unified_memory(monkeypatch):
    """統合メモリの内訳としてGPU確保分を返すことを確認する。

    GB10ではCUDAの確保分がRSSにもcgroupにも現れず、OS実使用(mem_used_gb)から
    漏れる。内訳を持たないと画面が実態の1/10を示してしまう。
    """
    monkeypatch.setattr(resources, "_hpc_slurm_free_resources", lambda: None)
    monkeypatch.setattr(
        resources,
        "_hpc_gpu_process_snapshot",
        lambda: (
            [
                {"pid": 1, "name": "Ollama", "username": "hpc-ollama", "memory_mb": 25053},
                {"pid": 2, "name": "python", "username": "user01", "memory_mb": None},
            ],
            True,
        ),
    )
    monkeypatch.setattr(
        resources.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=128 * 1024 ** 3, available=96 * 1024 ** 3),
    )
    monkeypatch.setattr(
        resources.psutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=1000 * 1024 ** 3, free=500 * 1024 ** 3),
    )

    snapshot = resources._hpc_resource_snapshot()

    assert round(snapshot["mem_gpu_used_gb"], 1) == 24.5
    # 統合メモリの内数であり、総量を超えない
    assert snapshot["mem_gpu_used_gb"] <= snapshot["mem_total_gb"]
    HpcResourceSnapshot.model_validate(snapshot)
