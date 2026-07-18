"""Slurmとリソース表示で使用する純粋な変換処理を検証する。"""

import pytest

from hpc_portal import resources


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

