"""接続先を使わずに、認証・所有権・実行条件の業務ルールを検証する。"""

import pytest

from hpc_portal.domain.errors import UseCaseError
from hpc_portal.domain.external_api.credentials import (
    ApiCredentials,
    ApiOwner,
    CredentialState,
)
from hpc_portal.domain.external_api.publication import (
    ApiPublication,
    ApiTarget,
)
from hpc_portal.domain.jobs.execution_policy import ExecutionPolicy
from hpc_portal.domain.jobs.execution_request import ExecutionRequest
from hpc_portal.domain.jobs.settings import JobSettings

OWNER = ApiOwner("alice", 1001, 11)


@pytest.mark.parametrize("state", [CredentialState.REVOKING, CredentialState.DISABLED])
def test_revocation_cannot_be_undone_by_issuance_or_rotation(state):
    credentials = ApiCredentials(OWNER, enabled=False, state=state)
    with pytest.raises(ValueError):
        credentials.complete_issuance()
    with pytest.raises(ValueError):
        credentials.begin_rotation("cloudflare")
    assert not credentials.available


def test_reenable_requires_completed_revocation():
    credentials = ApiCredentials(OWNER, state=CredentialState.READY)
    credentials.begin_revocation()
    assert not credentials.available
    with pytest.raises(ValueError):
        credentials.require_reenableable()
    credentials.complete_revocation()
    credentials.require_reenableable()
    assert not credentials.available


@pytest.mark.parametrize(
    "owner",
    [
        ApiOwner("alice", 1002, 11),
        ApiOwner("alice", 1001, 12),
        ApiOwner("bob", 1001, 11),
    ],
)
def test_account_recreation_does_not_inherit_publication(owner):
    publication = ApiPublication(OWNER, "analysis", ApiTarget(1001, 23000))
    with pytest.raises(ValueError):
        publication.require_owner(owner)


@pytest.mark.parametrize("target", [ApiTarget(1002, 23000), ApiTarget(1001, 80)])
def test_foreign_process_or_privileged_port_cannot_be_published(target):
    publication = ApiPublication(OWNER, "analysis", target)
    with pytest.raises(ValueError):
        publication.begin_publication()
    assert not publication.available


def test_gpu_sharing_and_unlimited_time_do_not_reserve_another_gpu():
    policy = ExecutionPolicy(JobSettings("0.1", "24.04", "2", "16G"))
    execution = ExecutionRequest("ubuntu-cli", "2", "4G", "UNLIMITED", True)
    available = {
        "cpu_available_count": 4,
        "mem_available_mb": 8192,
        "gpu_available_count": 0,
    }
    execution.require_resources(policy, available)
    with pytest.raises(UseCaseError):
        execution.require_resources(policy, {**available, "mem_available_mb": 1024})
