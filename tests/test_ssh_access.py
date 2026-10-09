"""HPC・Cloudflareへ接続せず、SSH許可の共有更新と中断時の復旧を検証する。"""

import asyncio
from types import SimpleNamespace

import pytest

from hpc_portal.application.usecase.ssh_access_usecase import SshAccessUseCase
from hpc_portal.infrastructure.config.ssh_access_settings import SshAccessSettings
from hpc_portal.infrastructure.persistence.encrypted_record_store import (
    EncryptedRecordStore,
)
from tests.test_external_api import FakeCF, FakeHub


class SshCloudflare(FakeCF):
    async def issue(self, name):
        """ユーザーごとに異なる識別子を発行する。

        Args:
            name: テスト対象の管理トークン名。

        Returns:
            仮の識別子と秘密値。
        """
        self.issued += 1
        return {
            "id": f"ssh-{self.issued}",
            "client_id": f"client-{self.issued}",
            "client_secret": f"secret-{self.issued}",
        }


@pytest.fixture
def ssh(tmp_path):
    """保存先と外部処理をテスト専用に置き換えたSSH操作を作る。

    Args:
        tmp_path: テスト専用の一時ディレクトリ。

    Returns:
        SSH操作、Cloudflareの偽物、二人のユーザー。
    """
    users = [SimpleNamespace(name="alice", id=11), SimpleNamespace(name="bob", id=12)]
    hub = FakeHub()
    hub.users = {user.name: user.id for user in users}
    cloudflare = SshCloudflare()
    service = SshAccessUseCase(
        config=SshAccessSettings(public_host="ssh.test"),
        store=EncryptedRecordStore(tmp_path / "ssh"),
        cloudflare=cloudflare,
        hub_users=hub,
        accounts=SimpleNamespace(
            getpwnam=lambda name: SimpleNamespace(
                pw_uid=1001 if name == "alice" else 1002
            ),
            linux_users_snapshot=lambda: [{"username": user.name} for user in users],
        ),
    )
    return service, cloudflare, users


async def test_shared_policy_preserves_other_users_and_is_opt_in(ssh):
    """未発行の人には発行せず、同時発行・再発行・失効でも他者の許可を残す。"""
    service, cf, (alice, bob) = ssh
    await service.synchronize()
    assert cf.issued == 0 and cf.apps[-1][2] == []

    await asyncio.gather(service.execute(alice, "issue"), service.execute(bob, "issue"))
    assert set(cf.apps[-1][2]) == {"ssh-1", "ssh-2"}
    bob_secret = service.record(bob)["client_secret"]
    await service.execute(alice, "rotate")
    assert service.record(bob)["client_secret"] == bob_secret
    await service.execute(alice, "revoke")
    await service.synchronize()
    assert cf.apps[-1][2] == ["ssh-2"] and cf.issued == 2
    assert service.record(alice)["state"] == "unissued"


async def test_policy_failure_recovers_and_admin_denial_blocks_reissue(ssh):
    """Access更新失敗は発行中として復旧し、管理者禁止を本人操作で解除しない。"""
    service, cf, (alice, _) = ssh
    cf.fail = True
    with pytest.raises(RuntimeError):
        await service.execute(alice, "issue")
    assert service.record(alice)["state"] == "issuing"
    cf.fail = False
    await service.synchronize()
    assert service.record(alice)["state"] == "ready" and cf.issued == 1

    cf.fail = True
    with pytest.raises(RuntimeError):
        await service.set_allowed(alice.name, False)
    assert (
        service.record(alice)["state"] == "revoking"
        and not service.record(alice)["enabled"]
    )
    with pytest.raises(ValueError):
        await service.execute(alice, "issue")
    cf.fail = False
    await service.synchronize()
    assert service.record(alice)["state"] == "disabled"
    await service.set_allowed(alice.name, True)
    assert service.record(alice)["state"] == "unissued" and cf.issued == 1


async def test_recreated_user_does_not_inherit_ssh_credentials(ssh):
    """同名でHub IDが変わったら旧トークンを失効し、新たな発行を待つ。"""
    service, cf, (alice, _) = ssh
    await service.execute(alice, "issue")
    service.hub_users.users[alice.name] = 99
    await service.synchronize()
    recreated = SimpleNamespace(name=alice.name, id=99)
    assert service.record(recreated)["state"] == "unissued"
    assert "ssh-1" in cf.removed and cf.issued == 1
