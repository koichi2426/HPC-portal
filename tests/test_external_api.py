"""Publication security and lifecycle without Cloudflare or an HPC connection."""

import asyncio
import json
import os
import pwd
import socket
import time
from types import SimpleNamespace

import aiohttp
import jwt
import psutil
import pytest
from aiohttp import web as aio_web
from cryptography.hazmat.primitives.asymmetric import rsa
from tornado import web

from hpc_portal.bootstrap.container import build_external_api_usecases
from hpc_portal.infrastructure.cloudflare.access_token_verifier import AccessVerifier
from hpc_portal.infrastructure.config.external_api_settings import ExternalApiSettings
from hpc_portal.infrastructure.http import api_relay
from hpc_portal.infrastructure.http.api_relay import clean_headers, request
from hpc_portal.infrastructure.linux import listener_inventory as inventory_module
from hpc_portal.infrastructure.linux import port_guard as network
from hpc_portal.infrastructure.linux.listener_inventory import (
    LinuxListenerInventory,
    candidate_id,
)
from hpc_portal.infrastructure.persistence.encrypted_record_store import (
    EncryptedRecordStore,
)
from hpc_portal.presentation.api_presenter import ApiPublicationPresenter
from hpc_portal.presentation.handlers import api_gateway, external_api
from hpc_portal.presentation.schemas.external_api import Registration


class FakeHub:
    def __init__(self):
        self.tokens, self.revoked, self.counter = {}, [], 0
        self.users = {}

    def issue(self, user):
        self.counter += 1
        self.users[user.name] = user.id
        self.tokens[self.counter] = user.id
        return {"hub_token": f"hub-secret-{self.counter}", "hub_token_id": self.counter}

    def revoke(self, token_id):
        self.revoked.append(token_id)
        self.tokens.pop(token_id, None)

    def valid(self, record, user):
        return self.tokens.get(record.get("hub_token_id")) == user.id

    def revoke_orphans(self, username, keep_id=None):
        for token_id, owner_id in list(self.tokens.items()):
            if owner_id == self.users.get(username) and token_id != keep_id:
                self.revoke(token_id)


class FakeCF:
    def __init__(self):
        self.issued, self.rotated, self.removed, self.apps, self.removed_apps = (
            0,
            0,
            [],
            [],
            [],
        )
        self.fail = False

    async def issue(self, name):
        self.issued += 1
        return {"id": "cf-id", "client_id": "cf-client", "client_secret": "cf-secret"}

    async def rotate(self, token_id):
        self.rotated += 1
        if self.fail:
            raise RuntimeError("response lost")
        return {
            "id": token_id,
            "client_id": "cf-client",
            "client_secret": f"cf-rotated-{self.rotated}",
        }

    async def app(self, *args):
        if self.fail:
            raise RuntimeError("outage")
        self.apps.append(args)
        return {"id": "app-id", "aud": "expected-aud"}

    async def remove_app(self, name, app_id=None):
        if self.fail:
            raise RuntimeError("outage")
        self.removed_apps.append((name, app_id))

    async def remove(self, token_id):
        self.removed.append(token_id)
        if self.fail:
            raise RuntimeError("outage")


@pytest.fixture
async def credentials(tmp_path):
    hub, cf = FakeHub(), FakeCF()
    service = make_external_api(
        EncryptedRecordStore(tmp_path / "credentials"),
        cf,
        hub,
        ExternalApiSettings(
            public_host="portal.test", access_issuer="https://team.cloudflareaccess.com"
        ),
    )
    service.queries.credential_identity = lambda user: {
        "uid": 1001,
        "hub_user_id": user.id,
    }
    user = SimpleNamespace(name="alice", id=11, api_tokens=[])
    await service.issue_credentials.execute(user)
    return service, user, hub, cf


def test_store_encrypts_and_restarts_with_same_key(tmp_path):
    directory = tmp_path / "private"
    store = EncryptedRecordStore(directory)
    store.put("credentials", "alice", {"secret": "must-not-be-plain"})
    assert b"must-not-be-plain" not in store.path.read_bytes()
    assert (
        EncryptedRecordStore(directory).get("credentials", "alice")["secret"]
        == "must-not-be-plain"
    )
    assert (directory / "key").stat().st_mode & 0o777 == 0o600
    assert store.path.stat().st_mode & 0o777 == 0o600
    (directory / "key").unlink()
    with pytest.raises(ValueError, match="暗号鍵"):
        EncryptedRecordStore(directory)


def test_ciphertext_cannot_be_moved_to_another_identity(tmp_path):
    store = EncryptedRecordStore(tmp_path / "private")
    store.put("credentials", "alice", {"secret": "private"})
    with store.connect() as db:
        db.execute("UPDATE records SET name='bob'")
    with pytest.raises(ValueError, match="identity"):
        store.get("credentials", "bob")


async def test_initial_sync_is_idempotent_and_does_not_publish_anything(credentials):
    service, user, hub, cf = credentials
    await asyncio.gather(
        service.issue_credentials.execute(user), service.issue_credentials.execute(user)
    )
    assert cf.issued == 1 and hub.counter == 1 and not cf.apps


async def test_independent_rotation_and_old_hub_token_revocation(credentials):
    service, user, hub, cf = credentials
    old = service.queries.credential_record(user)
    rotated = await service.rotate_credentials.execute(user, "cloudflare")
    assert rotated["hub_token"] == old["hub_token"]
    assert rotated["client_id"] == old["client_id"]
    assert rotated["client_secret"] != old["client_secret"]
    final = await service.rotate_credentials.execute(user, "jupyterhub")
    assert final["client_secret"] == rotated["client_secret"]
    assert final["hub_token"] != old["hub_token"]
    assert old["hub_token_id"] not in hub.tokens


async def test_lost_cloudflare_rotation_is_recovered_without_new_token(credentials):
    service, user, hub, cf = credentials
    cf.fail = True
    with pytest.raises(RuntimeError):
        await service.rotate_credentials.execute(user, "cloudflare")
    assert service.queries.credential_record(user)["state"] == "rotating_cloudflare"
    cf.fail = False
    await service.issue_credentials.execute(user)
    assert service.queries.credential_record(user)["client_secret"] == "cf-rotated-2"
    assert cf.issued == 1 and hub.counter == 1


@pytest.mark.parametrize("new_saved", [False, True])
async def test_interrupted_hub_rotation_finishes_revocation(credentials, new_saved):
    service, user, hub, cf = credentials
    record = service.queries.credential_record(user)
    old_id = record["hub_token_id"]
    if new_saved:
        record.update(hub.issue(user))
    record.update(state="rotating_jupyterhub", revoke_pending=[old_id])
    service.store.put("credentials", user.name, record)
    await service.issue_credentials.execute(user)
    assert old_id not in hub.tokens
    assert hub.valid(service.queries.credential_record(user), user)


async def test_disable_is_local_deny_during_cloudflare_outage(credentials):
    service, user, hub, cf = credentials
    old_id = service.queries.credential_record(user)["hub_token_id"]
    cf.fail = True
    with pytest.raises(RuntimeError):
        await service.revoke_credentials.execute(user.name)
    record = service.queries.credential_record(user)
    assert not record["enabled"] and record["state"] == "revoking"
    assert old_id not in hub.tokens
    cf.fail = False
    await service.revoke_credentials.execute(user.name)
    assert "hub_token" not in service.queries.credential_record(user)
    await service.issue_credentials.execute(user)
    assert cf.issued == 1


async def test_recreated_hub_identity_gets_fresh_credentials(credentials):
    service, user, hub, cf = credentials
    old = service.queries.credential_record(user)
    recreated = SimpleNamespace(name=user.name, id=99, api_tokens=[])
    with pytest.raises(ValueError):
        service.queries.credential_record(recreated)
    await service.issue_credentials.execute(recreated)
    assert old["hub_token_id"] not in hub.tokens
    assert service.queries.credential_record(recreated)["hub_user_id"] == 99


def target(uid=1001, port=23000, pid=91, stamp=10):
    row = {
        "uid": uid,
        "pid": pid,
        "started_at": stamp,
        "netns": os.stat("/proc/self/ns/net").st_ino,
        "inode": "socket:[12345]",
        "fd": 5,
        "port": port,
        "display_name": "my-app",
        "workdir": "/home/alice/project",
    }
    row["candidate"] = candidate_id(row)
    return row


class FakeInventory:
    def __init__(self):
        self.row = target()
        self.alive = True

    def choose(self, uid, candidate="", port=None):
        if (
            not self.alive
            or uid != self.row["uid"]
            or (candidate and candidate != self.row["candidate"])
            or (port and port != self.row["port"])
        ):
            raise ValueError("listener changed")
        return dict(self.row)

    def validate(self, old):
        return self.choose(old["uid"], old["candidate"], old["port"])

    def listeners(self, uid):
        return [dict(self.row)] if self.alive and self.row["uid"] == uid else []


class FakeGuard:
    def __init__(self):
        self.targets, self.fail = [], False

    async def protect(self, target):
        if self.fail:
            raise ValueError("guard failed")
        self.targets.append(target)

    async def check(self, target):
        if self.fail:
            raise ValueError("guard failed")


@pytest.fixture
async def publications(credentials, monkeypatch):
    creds, user, hub, cf = credentials
    monkeypatch.setattr(
        "pwd.getpwnam",
        lambda name: SimpleNamespace(
            pw_uid=1001 if name == "alice" else 1002, pw_dir="/home/" + name
        ),
    )
    inventory, guard = FakeInventory(), FakeGuard()
    registry = build_external_api_usecases(
        creds.store,
        cf,
        hub,
        creds.config,
        inventory,
        guard,
        AccessVerifier(creds.config),
        pwd,
        api_relay,
        lambda: [],
    )

    async def probe(record):
        await guard.check(record["target"])
        inventory.validate(record["target"])

    registry.publish_registration.health.check = probe
    return registry, user, cf, inventory, guard


async def test_publication_registers_only_owner_service_auth_and_preserves_url(
    publications,
):
    registry, user, cf, inventory, guard = publications
    settings = Registration(name="analysis", candidate=inventory.row["candidate"])
    first = await registry.publish_api.execute(user, settings)
    assert first["state"] == "published"
    name, paths, token_ids, old_id = cf.apps[-1]
    assert paths == [
        "portal.test/hub/user-api/alice/analysis",
        "portal.test/hub/user-api/alice/analysis/*",
    ]
    assert token_ids == ["cf-id"]
    url = ApiPublicationPresenter(registry.config, registry.accounts).publication_url(
        first
    )
    inventory.row = target(port=23001, pid=92)
    second = await registry.publish_api.execute(
        user, Registration(name="analysis", candidate=inventory.row["candidate"])
    )
    assert (
        second["target"]["port"] == 23001
        and ApiPublicationPresenter(registry.config, registry.accounts).publication_url(
            second
        )
        == url
    )
    assert cf.apps[-1][3] == "app-id"
    assert second["cf_aud"] == "expected-aud"


@pytest.mark.parametrize(
    "extra",
    [
        {"command": "node server.js"},
        {"url": "http://169.254.169.254/"},
        {"pid": 1},
        {"username": "bob"},
        {"workdir": "/root"},
    ],
)
def test_registration_rejects_launch_settings_and_untrusted_targets(extra):
    with pytest.raises(ValueError):
        Registration.model_validate({"name": "analysis", "port": 23000, **extra})


async def test_foreign_identity_and_stale_listener_cannot_be_registered(publications):
    registry, user, cf, inventory, guard = publications
    with pytest.raises(ValueError):
        await registry.publish_api.execute(
            user, Registration(name="analysis", candidate="unknown")
        )
    assert not registry.queries.list_publications(user)
    inventory.row = target(uid=1002)
    with pytest.raises(ValueError):
        await registry.publish_api.execute(
            user, Registration(name="analysis", port=23000)
        )


@pytest.mark.parametrize("failure", ["guard", "cloudflare"])
async def test_failure_never_enables_route_and_retry_recovers(publications, failure):
    registry, user, cf, inventory, guard = publications
    cf.fail = failure == "cloudflare"
    guard.fail = failure == "guard"
    with pytest.raises((RuntimeError, ValueError)):
        await registry.publish_api.execute(
            user, Registration(name="analysis", port=23000)
        )
    assert registry.queries.get_publication(user, "analysis")["state"] == "error"
    cf.fail = guard.fail = False
    await registry.refresh_publication.execute(
        registry.queries.get_publication(user, "analysis")
    )
    assert registry.queries.get_publication(user, "analysis")["state"] == "published"


async def test_same_port_reused_by_new_process_does_not_receive_requests(publications):
    registry, user, cf, inventory, guard = publications
    first = await registry.publish_api.execute(
        user, Registration(name="analysis", port=23000)
    )
    inventory.row = target(pid=92)
    await registry.refresh_publication.execute(first)
    assert registry.queries.get_publication(user, "analysis")["state"] == "disconnected"
    assert registry.queries.get_publication(user, "analysis")["target"]["pid"] == 91


async def test_unpublish_and_delete_do_not_stop_user_process_and_recover_remote_failure(
    publications,
):
    registry, user, cf, inventory, guard = publications
    await registry.publish_api.execute(user, Registration(name="analysis", port=23000))
    cf.fail = True
    with pytest.raises(RuntimeError):
        await registry.operate_publication.execute(user, "analysis", "delete")
    row = registry.queries.get_publication(user, "analysis")
    assert (
        row["state"] == "unpublished"
        and row["desired"] == "deleted"
        and inventory.alive
    )
    cf.fail = False
    await registry.refresh_publication.execute(row)
    assert not registry.queries.list_publications(user) and inventory.alive


def test_public_metadata_has_no_process_ids_starting_place_or_secrets(publications):
    registry, user, cf, inventory, guard = publications
    row = {
        "name": "analysis",
        "display_name": "analysis",
        "username": "alice",
        "target": inventory.row,
        "state": "published",
    }
    public = ApiPublicationPresenter(
        registry.config, registry.accounts
    ).publication_info(row)
    assert public["workdir"] == "~/project" and public["port"] == 23000
    assert not any(
        k in public
        for k in (
            "pid",
            "uid",
            "job_id",
            "node",
            "command",
            "client_secret",
            "candidate",
            "netns",
        )
    )


def conn(pid, port=23000, host="127.0.0.1", fd=5):
    return SimpleNamespace(
        pid=pid,
        fd=fd,
        status=psutil.CONN_LISTEN,
        family=socket.AF_INET,
        laddr=SimpleNamespace(ip=host, port=port),
    )


def test_inventory_filters_foreign_users_wildcards_and_reserved_ports(monkeypatch):
    config = ExternalApiSettings(reserved_ports=(23002,))
    inv = LinuxListenerInventory(config)
    rows = [conn(91), conn(92, 23001), conn(93, 23002), conn(94, 23003, "0.0.0.0")]
    monkeypatch.setattr(inv, "sockets", lambda: rows)
    monkeypatch.setattr(
        inventory_module,
        "process_snapshot",
        lambda pid: {
            k: v
            for k, v in target(uid=1002 if pid == 92 else 1001, pid=pid).items()
            if k in {"uid", "pid", "started_at", "display_name", "workdir", "netns"}
        },
    )
    real_readlink = os.readlink
    monkeypatch.setattr(
        os,
        "readlink",
        lambda path, *a, **kw: (
            "socket:[12345]"
            if str(path).startswith("/proc/")
            else real_readlink(path, *a, **kw)
        ),
    )
    assert sorted(row["port"] for row in inv.listeners(1001)) == [23000, 23003]
    assert inv.choose(1001, port=23000)["pid"] == 91
    rows.append(conn(92, 23000))
    with pytest.raises(ValueError):
        inv.choose(1001, port=23000)


def test_firewall_is_scoped_to_registered_ports_and_original_direction():
    script, fingerprint = network.ruleset([target()], existing=True)
    assert "flush ruleset" not in script
    assert "delete table inet hpc_api_guard" in script
    assert (
        'oifname "lo" ct direction original tcp dport 23000 meta skuid != { 0, 1001 }'
        in script
    )
    assert 'iifname != "lo" ct direction original tcp dport 23000' in script
    assert fingerprint in script
    assert "23001" not in script
    with pytest.raises(ValueError):
        network.ruleset([target(), target(uid=1002)])


async def test_guard_retains_protection_when_application_unpublished_and_recovers_reboot(
    tmp_path, monkeypatch
):
    inv = FakeInventory()
    store = EncryptedRecordStore(tmp_path / "state")
    guard = network.NftablesPortGuard(store, inv)
    calls = []

    async def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[-1] == "hpc_api_guard":
            return None
        return '{"nftables":[]}'

    monkeypatch.setattr(network, "command", run)
    await guard.protect(inv.row)
    assert store.get("guards", "23000")
    await guard.check(inv.row)
    assert sum(call[0] == ["nft", "-f", "-"] for call in calls) == 2
    inv.alive = False
    await guard.reconcile()
    assert not store.names("guards")


def test_headers_strip_credentials_and_connection_nominated_fields():
    headers = {
        "authorization": "secret",
        "cf-access-client-secret": "secret",
        "Cookie": "secret",
        "Cf-Access-Jwt-Assertion": "secret",
        "Forwarded": "secret",
        "X-Forwarded-Host": "internal",
        "connection": "X-Private",
        "X-Private": "secret",
        "Content-Type": "application/json",
    }
    assert clean_headers(headers) == {"Content-Type": "application/json"}


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def assertion(key, **overrides):
    claims = {
        "type": "app",
        "aud": ["expected-aud"],
        "iss": "https://team.cloudflareaccess.com",
        "iat": int(time.time()) - 1,
        "exp": int(time.time()) + 60,
        "common_name": "cf-client",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


async def test_access_verifies_signature_audience_issuer_expiration_and_owner(
    signing_key,
):
    verifier = AccessVerifier(
        ExternalApiSettings(access_issuer="https://team.cloudflareaccess.com")
    )
    verifier.cache = {"test-key": signing_key.public_key()}
    verifier.expires_at = time.monotonic() + 300
    app, creds = {"cf_aud": "expected-aud"}, {"client_id": "cf-client"}
    await verifier.verify(assertion(signing_key), app, creds)
    for change in [
        {"aud": ["other-aud"]},
        {"iss": "https://evil.test"},
        {"exp": 1},
        {"common_name": "other-user"},
        {"type": "org"},
    ]:
        with pytest.raises(ValueError):
            await verifier.verify(assertion(signing_key, **change), app, creds)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ValueError):
        await verifier.verify(assertion(other_key), app, creds)
    with pytest.raises(ValueError):
        await verifier.verify("", app, creds)


async def test_streamed_http_preserves_body_query_and_does_not_forward_credentials():
    seen = {}

    async def endpoint(req):
        seen.update(
            headers=dict(req.headers),
            body=await req.read(),
            query=req.query_string,
            path=req.path,
        )
        return aio_web.Response(
            body=b"x" * (128 * 1024),
            headers={"Set-Cookie": "private", "Location": "/next"},
        )

    app = aio_web.Application()
    app.router.add_post("/analyze", endpoint)
    runner = aio_web.AppRunner(app)
    await runner.setup()
    site = aio_web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    inv = LinuxListenerInventory(
        ExternalApiSettings(min_uid=os.getuid(), reserved_ports=())
    )
    try:
        target_row = await asyncio.to_thread(inv.choose, os.getuid(), "", port)
        async with request(
            inv,
            target_row,
            "POST",
            "/analyze?x=1",
            {
                "Authorization": "secret",
                "CF-Access-Client-Secret": "secret",
                "Content-Type": "application/json",
            },
            b'{"values":[1]}',
        ) as response:
            chunks = [chunk async for chunk in response.content.iter_chunked(65536)]
            assert sum(map(len, chunks)) == 128 * 1024
            assert "Set-Cookie" not in clean_headers(response.headers, response=True)
        assert seen["body"] == b'{"values":[1]}' and seen["query"] == "x=1"
        assert not any(
            k.lower() in {"authorization", "cf-access-client-secret"}
            for k in seen["headers"]
        )
    finally:
        await runner.cleanup()


async def test_connector_rechecks_identity_after_connection_before_sending_secrets():
    received = []

    async def endpoint(req):
        received.append(dict(req.headers))
        return aio_web.Response()

    app = aio_web.Application()
    app.router.add_get("/", endpoint)
    runner = aio_web.AppRunner(app)
    await runner.setup()
    site = aio_web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    inv = FakeInventory()
    inv.row["port"] = port
    calls = []

    def validate(row):
        calls.append(1)
        if len(calls) >= 2:
            raise ValueError("owner changed")

    inv.validate = validate
    try:
        with pytest.raises(aiohttp.ClientError):
            async with request(inv, inv.row, "GET", "/", {"X-Test": "must-not-leak"}):
                pass
        assert not received
    finally:
        await runner.cleanup()


class GatewayFake:
    def __init__(self, record, user):
        self.current_user = user
        self.token = SimpleNamespace(
            user=True,
            id=record["hub_token_id"],
            scopes=["custom:external-api:invoke!user=alice"],
        )
        self.request = SimpleNamespace(
            method="POST",
            query="x=1",
            body=b'{"values":[1]}',
            headers={
                "Authorization": "token secret",
                "CF-Access-Client-ID": record["client_id"],
                "CF-Access-Client-Secret": record["client_secret"],
                "Cf-Access-Jwt-Assertion": "assertion",
            },
            connection=SimpleNamespace(close=lambda: None),
        )
        self.headers, self.response, self.status = {}, bytearray(), None
        self._semaphore = asyncio.Semaphore(1)

    def get_token(self):
        return self.token

    def set_header(self, key, value):
        self.headers[key] = value

    def set_status(self, status):
        self.status = status

    def write(self, body):
        self.response.extend(body)

    async def flush(self):
        pass

    def finish(self):
        pass


@pytest.mark.parametrize(
    "failure",
    [
        "other_user",
        "scope",
        "hub_id",
        "cf_secret",
        "jwt",
        "disabled",
        "unpublished",
        "body",
    ],
)
async def test_gateway_denies_invalid_identity_or_publication(
    publications, monkeypatch, failure
):
    registry, user, cf, inventory, guard = publications
    await registry.publish_api.execute(user, Registration(name="analysis", port=23000))
    creds = registry
    handler = GatewayFake(creds.queries.credential_record(user), user)

    async def verify(*args):
        if failure == "jwt":
            raise ValueError("invalid assertion")

    registry.authorize_request.access_verifier.verify = verify
    if failure == "other_user":
        handler.current_user = SimpleNamespace(name="bob")
    if failure == "scope":
        handler.token.scopes = []
    if failure == "hub_id":
        handler.token.id = 999
    if failure == "cf_secret":
        handler.request.headers["CF-Access-Client-Secret"] = "old"
    if failure == "disabled":
        record = creds.queries.credential_record(user)
        record["enabled"] = False
        creds.store.put("credentials", "alice", record)
    if failure == "unpublished":
        await registry.operate_publication.execute(user, "analysis", "unpublish")
    if failure == "body":
        handler.request.body = b"x" * (registry.config.body_limit + 1)
    monkeypatch.setattr(api_gateway, "get_external_api", lambda: registry)
    with pytest.raises(web.HTTPError) as error:
        await api_gateway.ApiGateway.invoke(handler, "alice", "analysis", "analyze")
    assert error.value.status_code == (
        503 if failure == "unpublished" else 413 if failure == "body" else 403
    )


async def test_real_hub_tokens_have_only_custom_owner_scope_and_never_expire(
    monkeypatch,
):
    from jupyterhub import orm, roles, scopes
    from jupyterhub.user import UserDict

    from hpc_portal.infrastructure.jupyterhub.token_gateway import (
        HubTokenGateway as HubTokens,
    )

    monkeypatch.setattr(scopes, "scope_definitions", dict(scopes.scope_definitions))
    scopes.define_custom_scopes(
        {"custom:external-api:invoke": {"description": "Invoke own APIs"}}
    )
    db = orm.new_session_factory()()
    try:
        db.add(orm.OAuthClient(identifier="jupyterhub", secret="", redirect_uri=""))
        db.commit()
        for role in roles.get_default_roles():
            if role["name"] == "user":
                role["scopes"] = ["self", "custom:external-api:invoke!user"]
            roles.create_role(db, role)
        hub = SimpleNamespace(
            db=db,
            users=UserDict(lambda: db, {}),
            authenticator=SimpleNamespace(add_user=lambda user: None),
        )
        tokens = HubTokens(hub)
        user = await tokens.user("alice")
        issued = tokens.issue(user)
        record = orm.APIToken.find(db, issued["hub_token"])
        assert record.expires_at is None
        assert set(record.scopes) == {"custom:external-api:invoke!user=alice"}
        tokens.revoke(issued["hub_token_id"])
        assert orm.APIToken.find(db, issued["hub_token"]) is None
    finally:
        db.close()


async def test_browser_management_rejects_token_even_with_valid_user(monkeypatch):
    from jupyterhub.handlers.base import BaseHandler

    async def prepare(self):
        pass

    monkeypatch.setattr(BaseHandler, "prepare", prepare)
    fake = object.__new__(external_api.BrowserHandler)
    fake._token_authenticated = True
    monkeypatch.setattr(
        external_api.BrowserHandler, "get_auth_token", lambda self: "valid-token"
    )
    with pytest.raises(web.HTTPError) as exc:
        await external_api.BrowserHandler.prepare(fake)
    assert exc.value.status_code == 403


async def test_cloudflare_creates_service_only_policies_and_recovers_existing_app():
    from hpc_portal.infrastructure.cloudflare.access_client import (
        CloudflareAccessClient,
    )

    cf = CloudflareAccessClient(ExternalApiSettings())
    calls = []

    async def listing(path):
        return []

    async def run(method, path, data=None, **kwargs):
        calls.append((method, path, data))
        return {"id": "app-id", "aud": "expected-aud"}

    cf.listing, cf.request = listing, run
    result = await cf.app(
        "HPC API / alice / analysis",
        ["portal.test/one", "portal.test/one/*"],
        ["owner-token"],
    )
    assert result == {"id": "app-id", "aud": "expected-aud"}
    policy = calls[-1][2]["policies"]
    assert len(policy) == 1 and policy[0]["decision"] == "non_identity"
    assert policy[0]["include"] == [{"service_token": {"token_id": "owner-token"}}]
    assert "allow" not in json.dumps(policy).lower()
    await cf.issue("owner-token")
    assert calls[-1][2]["duration"] == "forever"
    await cf.rotate("owner-token")
    assert calls[-1][:2] == ("POST", "/service_tokens/owner-token/rotate")


async def test_cloudflare_limit_does_not_create_shared_credentials():
    from hpc_portal.infrastructure.cloudflare.access_client import (
        CloudflareAccessClient,
        CloudflareError,
    )

    cf = CloudflareAccessClient(ExternalApiSettings(token_limit=1))

    async def listing(path):
        return [{"name": "other", "id": "other-id"}]

    cf.listing = listing
    with pytest.raises(CloudflareError, match="上限"):
        await cf.issue("new-user")


async def test_user_deletion_cleans_publications_without_stopping_process(
    publications, monkeypatch
):
    registry, user, cf, inventory, guard = publications
    await registry.publish_api.execute(user, Registration(name="analysis", port=23000))
    monkeypatch.setenv("HPC_EXTERNAL_API_ENABLED", "true")
    await registry.disable_user.execute("alice")
    assert (
        registry.queries.get_publication(user, "analysis")["state"] == "unpublished"
        and inventory.alive
    )
    await registry.delete_user_records.execute("alice")
    assert not registry.queries.list_publications(user) and inventory.alive


async def test_access_downloads_chunked_keys_caches_and_rejects_oversized(signing_key):
    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    key["kid"] = "test-key"
    payload = json.dumps({"keys": [key]}).encode()
    calls = []

    async def keys(req):
        calls.append(1)
        response = aio_web.StreamResponse()
        await response.prepare(req)
        for offset in range(0, len(payload), 100):
            await response.write(payload[offset : offset + 100])
            await asyncio.sleep(0.001)
        await response.write_eof()
        return response

    app = aio_web.Application()
    app.router.add_get("/cdn-cgi/access/certs", keys)
    runner = aio_web.AppRunner(app)
    await runner.setup()
    site = aio_web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    verifier = AccessVerifier(
        ExternalApiSettings(access_issuer=f"http://127.0.0.1:{port}")
    )
    try:
        resolved = await verifier.key("test-key")
        assert resolved.public_numbers() == signing_key.public_key().public_numbers()
        await verifier.key("test-key")
        assert len(calls) == 1
        payload = b"x" * 65537
        verifier.expires_at = 0
        with pytest.raises(ValueError):
            await verifier.key("test-key")
    finally:
        await runner.cleanup()


async def test_gateway_real_http_uses_hub_token_auth_without_browser_cookie(
    monkeypatch, signing_key, tmp_path
):
    import logging
    import pwd

    from jupyterhub import orm, roles, scopes
    from jupyterhub.auth import DummyAuthenticator
    from jupyterhub.user import UserDict
    from tornado.httpclient import AsyncHTTPClient
    from tornado.httpserver import HTTPServer
    from tornado.netutil import bind_sockets

    username = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setattr(scopes, "scope_definitions", dict(scopes.scope_definitions))
    scopes.define_custom_scopes(
        {"custom:external-api:invoke": {"description": "Invoke own APIs"}}
    )
    db = orm.new_session_factory()()
    upstream_runner = None
    server = None
    sockets = []
    try:
        db.add(orm.OAuthClient(identifier="jupyterhub", secret="", redirect_uri=""))
        user_row = orm.User(name=username)
        db.add(user_row)
        db.commit()
        for role in roles.get_default_roles():
            if role["name"] == "user":
                role["scopes"] = ["self", "custom:external-api:invoke!user"]
            roles.create_role(db, role)
        roles.assign_default_roles(db, user_row)
        users = UserDict(lambda: db, {})
        user = users[user_row]
        raw_token = user.new_api_token(
            scopes=[f"custom:external-api:invoke!user={username}"]
        )
        token = orm.APIToken.find(db, raw_token)
        record = {
            "enabled": True,
            "state": "ready",
            "hub_token_id": token.id,
            "client_id": "cf-client",
            "client_secret": "cf-secret",
        }

        seen = {}

        async def analyze(req):
            seen.update(
                headers=dict(req.headers), path=req.path, query=req.query_string
            )
            body = await req.json()
            return aio_web.json_response({"sum": sum(body["values"])})

        upstream = aio_web.Application()
        upstream.router.add_post("/api/analyze", analyze)
        upstream_runner = aio_web.AppRunner(upstream)
        await upstream_runner.setup()
        site = aio_web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await site.start()
        upstream_port = site._server.sockets[0].getsockname()[1]
        inventory = LinuxListenerInventory(
            ExternalApiSettings(min_uid=os.getuid(), reserved_ports=())
        )
        target_row = await asyncio.to_thread(
            inventory.choose, os.getuid(), "", upstream_port
        )

        verifier = AccessVerifier(
            ExternalApiSettings(access_issuer="https://team.cloudflareaccess.com")
        )
        verifier.cache = {"test-key": signing_key.public_key()}
        verifier.expires_at = time.monotonic() + 300

        async def check(target):
            pass

        store = EncryptedRecordStore(tmp_path / "gateway")
        record.update(uid=os.getuid(), hub_user_id=user.id)
        store.put("credentials", username, record)
        store.put(
            "publications",
            username + "/analysis",
            {
                "username": username,
                "uid": os.getuid(),
                "hub_user_id": user.id,
                "name": "analysis",
                "state": "published",
                "desired": "published",
                "cf_aud": "expected-aud",
                "target": target_row,
            },
        )
        registry = build_external_api_usecases(
            store,
            None,
            None,
            ExternalApiSettings(),
            inventory,
            SimpleNamespace(check=check),
            verifier,
            pwd,
            api_relay,
            lambda: [],
        )
        monkeypatch.setattr(api_gateway, "get_external_api", lambda: registry)
        application = web.Application(
            [(r"/hub/user-api/([^/]+)/(analysis)/(.*)", api_gateway.ApiGateway)],
            db=db,
            users=users,
            authenticator=DummyAuthenticator(auth_refresh_age=0),
            hub=SimpleNamespace(
                base_url="/hub/", host="", cookie_name="jupyterhub-hub-login"
            ),
            cookie_secret="test-cookie-secret",
            xsrf_cookies=True,
            log=logging.getLogger("test.gateway"),
        )
        sockets = bind_sockets(0, address="127.0.0.1")
        server = HTTPServer(application)
        server.add_sockets(sockets)
        portal_port = sockets[0].getsockname()[1]
        headers = {
            "Authorization": "token " + raw_token,
            "CF-Access-Client-ID": "cf-client",
            "CF-Access-Client-Secret": "cf-secret",
            "Cf-Access-Jwt-Assertion": assertion(signing_key),
            "Content-Type": "application/json",
        }
        client = AsyncHTTPClient()
        response = await client.fetch(
            f"http://127.0.0.1:{portal_port}/hub/user-api/{username}/analysis/api/analyze?mode=test",
            method="POST",
            headers=headers,
            body='{"values":[1,2,3]}',
            raise_error=False,
        )
        assert response.code == 200, response.body.decode()
        assert json.loads(response.body) == {"sum": 6}
        assert seen["path"] == "/api/analyze" and seen["query"] == "mode=test"
        assert not any(
            key.lower() in {"authorization", "cf-access-client-secret"}
            for key in seen["headers"]
        )
        for bad_headers in [
            {},
            {**headers, "Cf-Access-Jwt-Assertion": "forged"},
            {**headers, "CF-Access-Client-Secret": "old"},
        ]:
            response = await client.fetch(
                f"http://127.0.0.1:{portal_port}/hub/user-api/{username}/analysis/api/analyze",
                method="POST",
                headers=bad_headers,
                body='{"values":[1]}',
                raise_error=False,
            )
            assert response.code == 403
    finally:
        if server:
            server.stop()
            await server.close_all_connections()
        for sock in sockets:
            sock.close()
        if upstream_runner:
            await upstream_runner.cleanup()
        db.close()


async def test_guard_keeps_port_protected_after_selected_worker_exits(
    tmp_path, monkeypatch
):
    inv = FakeInventory()
    store = EncryptedRecordStore(tmp_path / "state")
    guard = network.NftablesPortGuard(store, inv)

    async def run(argv, **kwargs):
        return None if argv[-1] == "hpc_api_guard" else '{"nftables":[]}'

    monkeypatch.setattr(network, "command", run)
    await guard.protect(inv.row)
    old = dict(inv.row)
    inv.row = target(pid=92, stamp=11)
    # Original socket has gone, but a sibling or restarted same-owner app still occupies the port.
    with pytest.raises(ValueError):
        inv.validate(old)
    await guard.reconcile()
    assert store.get("guards", "23000")["candidate"] == old["candidate"]
    inv.alive = False
    await guard.reconcile()
    assert not store.names("guards")


def make_external_api(store, cloudflare, hub, config):
    return build_external_api_usecases(
        store, cloudflare, hub, config, None, None, None, pwd, api_relay, lambda: []
    )
