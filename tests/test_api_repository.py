"""既存の暗号化レコードを維持したまま、業務モデルの状態を保存する。"""

import pytest

from hpc_portal.domain.external_api.credentials import ApiOwner
from hpc_portal.infrastructure.persistence.api_aggregate_repository import (
    ApiAggregateRepository,
)
from hpc_portal.infrastructure.persistence.encrypted_record_store import (
    EncryptedRecordStore,
)


def test_credential_state_update_preserves_provider_data_and_encryption(tmp_path):
    store = EncryptedRecordStore(tmp_path / "state")
    original = {
        "uid": 1001,
        "hub_user_id": 11,
        "enabled": True,
        "state": "ready",
        "client_secret": "private-client-secret",
        "hub_token": "private-hub-token",
        "cf_token_id": "cf-id",
        "hub_token_id": 23,
        "revoke_pending": [24],
        "updated_at": 123,
    }
    store.put("credentials", "alice", original)
    repository = ApiAggregateRepository(store)
    credentials = repository.load_credentials("alice")
    credentials.begin_rotation("cloudflare")
    repository.save_credentials(credentials)

    restored = EncryptedRecordStore(tmp_path / "state")
    assert restored.get("credentials", "alice") == {
        **original,
        "state": "rotating_cloudflare",
    }
    assert b"private-client-secret" not in store.path.read_bytes()
    assert b"private-hub-token" not in store.path.read_bytes()


def test_publication_stop_preserves_target_and_remote_cleanup_identity(tmp_path):
    store = EncryptedRecordStore(tmp_path / "state")
    original = {
        "username": "alice",
        "uid": 1001,
        "hub_user_id": 11,
        "name": "analysis",
        "state": "published",
        "desired": "published",
        "target": {"uid": 1001, "port": 23000, "candidate": "original-listener"},
        "cf_app_id": "remote-app",
        "cf_aud": "audience",
        "remote_clean": False,
    }
    store.put("publications", "alice/analysis", original)
    repository = ApiAggregateRepository(store)
    publication = repository.load_publication("alice", "analysis")
    publication.stop(delete=True)
    repository.save_publication(publication)
    assert store.get("publications", "alice/analysis") == {
        **original,
        "state": "unpublished",
        "desired": "deleted",
    }


def test_stale_aggregate_cannot_overwrite_recreated_account(tmp_path):
    store = EncryptedRecordStore(tmp_path / "state")
    record = {"uid": 1001, "hub_user_id": 11, "enabled": True, "state": "ready"}
    store.put("credentials", "alice", record)
    repository = ApiAggregateRepository(store)
    credentials = repository.load_credentials("alice")
    credentials.owner = ApiOwner("alice", 1001, 99)
    with pytest.raises(ValueError):
        repository.save_credentials(credentials)
    assert store.get("credentials", "alice") == record
