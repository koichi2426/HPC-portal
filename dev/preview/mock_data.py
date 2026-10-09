"""画面確認専用のサンプルデータと、メモリ上の操作結果。"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

from hpc_portal.domain.jobs.execution_policy import ExecutionPolicy
from hpc_portal.domain.jobs.settings import JobSettings, runtime_from_hours_choice
from hpc_portal.presentation.job_allocation import allocation_summary
from hpc_portal.presentation.job_form_renderer import JobFormSettings
from hpc_portal.presentation.schemas.responses.resources import HpcResourceSnapshot

FORM_SETTINGS = JobFormSettings(
    jupyter_ubuntu_version="24.04",
    ollama_allowed_context_lengths=("32768", "65536", "131072", "262144"),
    ollama_allowed_cpus=("4", "8", "12", "16", "20"),
    ollama_allowed_keep_alive=("5m", "30m", "1h", "-1"),
    ollama_allowed_kv_cache_types=("f16", "q8_0", "q4_0"),
    ollama_allowed_max_loaded_models=("1", "2"),
    ollama_allowed_max_queue=("32", "64", "128", "256"),
    ollama_allowed_memory=(
        "16G",
        "24G",
        "32G",
        "40G",
        "48G",
        "64G",
        "80G",
        "96G",
        "112G",
    ),
    ollama_allowed_parallel=("1", "2", "4"),
    ollama_default_context_length="131072",
    ollama_default_cpus="8",
    ollama_default_flash_attention=True,
    ollama_default_keep_alive="30m",
    ollama_default_kv_cache_type="q8_0",
    ollama_default_max_loaded_models="1",
    ollama_default_max_queue="64",
    ollama_default_memory="40G",
    ollama_default_parallel="2",
    ollama_version="0.18.0",
    openwebui_version="0.8.0",
    public_scheme="http",
    job_dns_domain="localhost",
)


SCENARIOS = {
    "normal": "通常",
    "empty": "データなし",
    "pending": "起動中",
    "error": "エラー",
    "disabled": "API利用停止",
    "busy": "リソース不足",
}


@dataclass
class MockUser:
    name: str
    spawners: dict = field(default_factory=dict)
    admin: bool = False

    @property
    def escaped_name(self):
        return self.name

    @property
    def json_escaped_name(self):
        return json.dumps(self.name)[1:-1]

    @property
    def base_url(self):
        return f"/user/{self.name}/"

    @property
    def spawner(self):
        return SimpleNamespace(options_form=True)


def account_row(name, uid, *, admin=False, display_name=""):
    return {
        "username": name,
        "display_name": display_name,
        "uid": uid,
        "home": f"/home/{name}",
        "protected": admin,
        "sudo_enabled": admin,
        "api_access": "enabled",
        "api_access_message": "",
        "external_api_enabled": True,
        "external_api_state": "unissued",
        "ssh_access_enabled": True,
        "ssh_access_state": "unissued",
        "storage_used_bytes": 3 * 1024**3,
        "storage_used_label": "3.0 GB",
        "storage_message": "",
    }


class MockState:
    """外部サービスに触れず、画面操作後の状態を再読み込みまで保持する。"""

    def __init__(self):
        self.reset()

    def reset(self, scenario="normal"):
        if scenario not in SCENARIOS:
            raise ValueError("表示パターンが不正です")
        self.scenario = scenario
        self.sequence = 0
        self.accounts = {
            name: account_row(name, uid, admin=admin, display_name=label)
            for name, uid, admin, label in (
                ("alice", 1001, False, "サンプルユーザー"),
                ("bob", 1002, False, "共同利用ユーザー"),
                ("admin", 1000, True, "管理者"),
            )
        }
        self.users = {
            name: MockUser(name, admin=row["protected"])
            for name, row in self.accounts.items()
        }
        self.publications = {name: {} for name in self.users}
        self.tokens = {name: {} for name in self.users}
        self.credentials = {name: self._credentials(name) for name in self.users}
        self.ssh_credentials = {}
        self.models = [] if scenario == "empty" else ["qwen3:8b", "gemma3:4b"]
        self.ollama = {
            "running": scenario != "empty",
            "api": scenario not in {"empty", "pending", "error"},
            "job_ids": ["12345"] if scenario != "empty" else [],
            "cpus": "4",
            "memory": "32G",
            "parallel": "1",
            "max_loaded_models": "1",
            "context_length": "32768",
            "kv_cache_type": "q8_0",
            "keep_alive": "5m",
            "max_queue": "128",
            "flash_attention": "1",
            "version": "0.18.0",
            "latest_version": "0.18.1",
            "update_available": True,
            "update_state": "",
            "update_message": "",
            "update_error": "",
        }
        self.pull = {"state": "idle"}
        if scenario != "empty":
            for user in self.users.values():
                self.start_app(
                    user, "notebook", {"app_choice": "ubuntu-cli"}, seeded=True
                )
                self.start_app(user, "chat", {"app_choice": "open-webui"}, seeded=True)
                self.register_publication(
                    user.name,
                    {
                        "name": "analyze",
                        "display_name": "解析 API",
                        "port": 3000,
                        "health_path": "/health",
                        "candidate": "mock-analysis",
                    },
                )
        if scenario == "disabled":
            for row in self.accounts.values():
                row.update(
                    api_access="disabled",
                    external_api_enabled=False,
                    external_api_state="disabled",
                )

    def next_id(self):
        self.sequence += 1
        return str(self.sequence)

    def user(self, name):
        user = self.users[name]
        for spawner in user.spawners.values():
            if (
                spawner.pending
                and spawner.ready_at
                and time.monotonic() >= spawner.ready_at
            ):
                spawner.pending = None
        return user

    def resource_snapshot(self):
        busy = self.scenario == "busy"
        cpu_free, memory_free = (1, 2) if busy else (14, 88)
        return HpcResourceSnapshot(
            cpu_available=cpu_free / 20 * 100,
            cpu_available_count=cpu_free,
            cpu_total=20,
            cpu_status="残りわずか" if busy else "利用可能",
            mem_available=memory_free / 128 * 100,
            mem_available_gb=memory_free,
            mem_used_gb=40,
            mem_total_gb=128,
            mem_gpu_used_gb=12,
            mem_status="利用可能",
            mem_slurm_available=memory_free / 128 * 100,
            mem_slurm_available_gb=memory_free,
            mem_slurm_used_gb=128 - memory_free,
            mem_slurm_total_gb=128,
            mem_slurm_status="残りわずか" if busy else "利用可能",
            disk_available=75,
            disk_available_gb=750,
            disk_total_gb=1000,
            disk_status="利用可能",
            gpu_max=1,
            gpu_available=100,
            gpu_available_count=1,
            gpu_status="共有利用可",
            gpu_processes=[],
            gpu_process_count=0,
            gpu_processes_available=True,
            updated_at=time.time(),
        ).model_dump()

    def recommendations(self):
        return ExecutionPolicy(
            JobSettings(
                openwebui_version="0.8.0",
                jupyter_ubuntu_version="24.04",
                ollama_default_cpus="4",
                ollama_default_memory="32G",
            )
        ).app_resource_recommendations()

    def start_app(self, user, name, form, *, seeded=False):
        choice = form.get("app_choice", "ubuntu-cli")
        if choice not in {"ubuntu-cli", "open-webui"}:
            raise ValueError("アプリ種別が不正です")
        runtime, _ = runtime_from_hours_choice(form.get("hours", "2"))
        cpu, memory = int(form.get("cpu", "2")), int(form.get("mem", "4"))
        if cpu < 1 or memory < 1:
            raise ValueError("CPU・メモリは1以上で指定してください")
        if not seeded:
            resource = self.resource_snapshot()
            if (
                cpu > resource["cpu_available_count"]
                or memory > resource["mem_slurm_available_gb"]
            ):
                raise ValueError("現在の空きリソースでは起動できません")
        spawner = SimpleNamespace(
            user=user,
            active=True,
            pending="spawn" if not seeded or self.scenario == "pending" else None,
            ready_at=time.monotonic() + 3 if not seeded else None,
            # jobサブドメインへの移動を避け、JUMPはこのサーバー内の説明画面へ向ける。
            job_id="",
            port=23000,
            server=None,
            public_url=f"/hub/preview/app/{name}",
            user_options={
                "app_choice": choice,
                "nprocs": str(cpu),
                "memory": f"{memory}G",
                "gpu": form.get("gpu", "0"),
                "runtime": runtime,
                "openwebui_version": "0.8.0",
                "ubuntu_version": "24.04",
            },
            hpc_failure_message="モックの起動エラーです。設定を確認して再試行できます。"
            if seeded and self.scenario == "error"
            else "",
        )
        if spawner.hpc_failure_message:
            spawner.active = False
        user.spawners[name] = spawner
        return spawner

    def memory_usage(self, user):
        return {
            name: {
                "memory_used_label": "5.2 GB" if self.scenario == "busy" else "1.2 GB",
                "gpu_memory_label": "",
                "memory_overuse_level": "warning" if self.scenario == "busy" else "",
                "memory_overuse_label": "要求メモリの130%"
                if self.scenario == "busy"
                else "",
            }
            for name, spawner in user.spawners.items()
            if spawner.active
        }

    def app_detail(self, user, name):
        spawner = user.spawners[name]
        allocation = allocation_summary(spawner.user_options)
        return {
            "shared_ollama": False,
            "server_name": name,
            "server_name_path": name,
            "app_label": allocation["app_label"],
            "app_choice": spawner.user_options["app_choice"],
            "status": "pending" if spawner.pending else "running",
            "status_label": "起動中" if spawner.pending else "実行中",
            "pending": spawner.pending,
            "active": spawner.active,
            "job_id": "",
            "job_host": "",
            "job_url": spawner.public_url,
            "port": spawner.port,
            "allocation": allocation,
            "openwebui_version": "0.8.0",
            "openwebui_target_version": "0.8.0",
            "ubuntu_version": "24.04",
            "ubuntu_target_version": "24.04",
            **self.memory_usage(user).get(name, {}),
        }

    def admin_apps(self):
        apps = []
        for name in self.users:
            user = self.user(name)
            for server_name, spawner in user.spawners.items():
                if not spawner.active:
                    continue
                allocation = allocation_summary(spawner.user_options)
                apps.append(
                    {
                        "job_id": str(12000 + len(apps)),
                        "username": name,
                        "display_name": self.accounts[name]["display_name"],
                        "app": allocation["app_label"],
                        "state": "PENDING" if spawner.pending else "RUNNING",
                        "state_label": "起動中" if spawner.pending else "実行中",
                        "cpus": allocation["cpu"],
                        "memory": allocation["memory"],
                        "gpus": allocation["gpu"],
                        "elapsed": "00:12:34",
                        "started_at": "2026-01-01T12:00:00",
                        "memory_used_label": "1.2 GB",
                        "gpu_memory_label": "—",
                        "cpu_memory_label": "1.2 GB",
                        "memory_overuse_level": "",
                        "memory_overuse_label": "",
                    }
                )
        return {"apps": apps, "error": "", "updated_at": time.time()}

    def ports(self):
        listeners = (
            []
            if self.scenario == "empty"
            else [
                {
                    "candidate": "mock-analysis",
                    "display_name": "server.py（Python）",
                    "workdir": "~/projects/analysis",
                    "port": 3000,
                    "started_at": time.time() - 600,
                    "state": "待ち受け中",
                },
                {
                    "candidate": "mock-worker",
                    "display_name": "server.js（Node.js）",
                    "workdir": "~/projects/worker",
                    "port": 8080,
                    "started_at": time.time() - 300,
                    "state": "待ち受け中",
                },
            ]
        )
        return {
            "listeners": listeners,
            "free": [3001, 3002, 3003, 8081],
            "range": [3000, 9000],
            "reserved": [8000, 8001, 8888],
            "checked_at": time.time(),
        }

    def register_publication(self, username, data):
        target = next(
            (
                row
                for row in self.ports()["listeners"]
                if row["candidate"] == data.get("candidate")
                or row["port"] == data.get("port")
            ),
            None,
        )
        if target is None:
            raise ValueError("待ち受け中のモックアプリを選択してください")
        name = data["name"]
        record = {
            "name": name,
            "display_name": data["display_name"],
            "state": "error" if self.scenario == "error" else "published",
            "health_path": data.get("health_path", "/health"),
            "checked_at": time.time(),
            "url": f"https://portal.example.com/hub/user-api/{username}/{name}/",
            "workdir": target["workdir"],
            "port": target["port"],
            "started_at": target["started_at"],
        }
        self.publications[username][name] = record
        return record

    def _credentials(self, username):
        return {
            "version": 1,
            "username": username,
            "client_id": f"mock-{username}.access",
            "client_secret": f"mock-cloudflare-{self.next_id()}",
            "jupyterhub_token": f"mock-jupyterhub-{self.next_id()}",
            "enabled": True,
            "service_state": "unissued",
            "hub_state": "ready",
            "expires": "無期限",
            "updated_at": time.time(),
            "base_url": "https://portal.example.com",
        }

    def credential_payload(self, username, action):
        record = self.credentials[username]
        if action in {"issue", "rotate_cloudflare"}:
            record["service_state"] = "ready"
            record.update(
                client_id=f"mock-{username}-{self.next_id()}.access",
                client_secret=f"mock-cloudflare-{self.next_id()}",
                updated_at=time.time(),
            )
        elif action == "rotate_jupyterhub":
            record["hub_state"] = "ready"
            record.update(
                jupyterhub_token=f"mock-jupyterhub-{self.next_id()}",
                updated_at=time.time(),
            )
        elif action == "revoke_cloudflare":
            record.update(service_state="unissued", client_id="", client_secret="")
        elif action == "revoke_jupyterhub":
            record.update(hub_state="revoked", jupyterhub_token="")
        record["enabled"] = self.accounts[username]["external_api_enabled"]
        self.accounts[username]["external_api_state"] = record["service_state"]
        return {
            **record,
            "client_id": record["client_id"]
            if record["service_state"] == "ready"
            else "",
            "client_secret": record["client_secret"]
            if record["service_state"] == "ready"
            else "",
            "apis": {
                name: row["url"] for name, row in self.publications[username].items()
            },
        }

    def ssh_payload(self, username, action):
        """本人のSSH発行・再発行・失効をモック内で再現する。

        Args:
            username: 操作するモックユーザー。
            action: 発行・表示などの操作名。

        Returns:
            実際の秘密値を含まないSSH設定。
        """
        record = self.ssh_credentials.setdefault(
            username,
            {
                "hostname": "ssh.example.com",
                "username": username,
                "port": 22,
                "enabled": True,
                "state": "unissued",
                "client_id": "",
                "client_secret": "",
            },
        )
        record["enabled"] = self.accounts[username]["ssh_access_enabled"]
        if action in {"issue", "rotate"}:
            if not record["enabled"]:
                raise ValueError("管理者がSSH公開を停止しています")
            record.update(
                state="ready",
                client_id=f"mock-ssh-{username}",
                client_secret=f"mock-secret-{self.next_id()}",
            )
        elif action == "revoke":
            record.update(state="unissued", client_id="", client_secret="")
        elif action not in {"reveal", "download"}:
            raise ValueError("操作が不正です")
        self.accounts[username]["ssh_access_state"] = record["state"]
        return dict(record)

    def account_action(self, current, data):
        action, name = data["action"], data.get("username", "")
        if action == "create":
            if (
                not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,31}", name)
                or name in self.accounts
            ):
                raise ValueError("ユーザー名が不正、または登録済みです")
            self.accounts[name] = account_row(
                name,
                max(row["uid"] for row in self.accounts.values()) + 1,
                display_name=data.get("display_name", ""),
            )
            self.accounts[name]["sudo_enabled"] = bool(data.get("sudo"))
            self.users[name] = MockUser(name)
            self.publications[name], self.tokens[name] = {}, {}
            self.credentials[name] = self._credentials(name)
            return {
                "ok": True,
                "username": name,
                "initial_password": "Mock-password-123!",
                "api_key": "mock-llm-key",
            }
        if name not in self.accounts:
            raise ValueError("ユーザーが見つかりません")
        row = self.accounts[name]
        if action in {"delete", "sudo_disable"} and (
            name == current or row["protected"]
        ):
            raise ValueError("自分自身や保護ユーザーには操作できません")
        if action == "delete":
            for records in (
                self.accounts,
                self.users,
                self.publications,
                self.tokens,
                self.credentials,
            ):
                del records[name]
            self.ssh_credentials.pop(name, None)
        elif action == "display_name":
            row["display_name"] = data.get("display_name", "")
        elif action == "password_regenerate":
            return {"ok": True, "initial_password": "Mock-password-123!"}
        elif action in {"sudo_enable", "sudo_disable"}:
            row["sudo_enabled"] = action == "sudo_enable"
        elif action in {"api_enable", "api_disable"}:
            row["api_access"] = "enabled" if action == "api_enable" else "disabled"
        elif action in {"external_api_enable", "external_api_disable"}:
            row["external_api_enabled"] = action == "external_api_enable"
            row["external_api_state"] = (
                "unissued" if row["external_api_enabled"] else "disabled"
            )
            enabled = row["external_api_enabled"]
            self.credentials[name].update(
                enabled=enabled,
                service_state=row["external_api_state"],
                client_id="",
                client_secret="",
                hub_state="ready" if enabled else "revoked",
                jupyterhub_token=f"mock-jupyterhub-{self.next_id()}" if enabled else "",
            )
        elif action in {"ssh_access_enable", "ssh_access_disable"}:
            row["ssh_access_enabled"] = action == "ssh_access_enable"
            row["ssh_access_state"] = (
                "unissued" if row["ssh_access_enabled"] else "disabled"
            )
            if name in self.ssh_credentials:
                self.ssh_credentials[name].update(
                    enabled=row["ssh_access_enabled"],
                    state=row["ssh_access_state"],
                    client_id="",
                    client_secret="",
                )
        else:
            raise ValueError("操作が不正です")
        return {"ok": True}

    def ollama_detail(self):
        status = self.ollama
        return {
            "shared_ollama": True,
            "server_name": "shared-ollama",
            "app_choice": "shared-ollama",
            "app_label": "Ollama",
            "active": status["running"],
            "api": status["api"],
            "pending": None,
            "status": "running" if status["running"] else "stopped",
            "status_label": "実行中" if status["running"] else "停止中",
            "job_id": ", ".join(status["job_ids"]),
            "job_host": "",
            "job_url": "",
            "allocation": {
                "cpu": status["cpus"],
                "memory": status["memory"],
                "gpu": 1,
                "gpu_label": "1 GPU",
                "runtime": "UNLIMITED",
                "hours": "無制限",
            },
            "models_dir": "/opt/mock/models",
            "port": 11434,
            "version": status["version"],
            "bootstrap_version": "0.18.0",
            "latest_version": status["latest_version"],
            "update_available": status["update_available"],
            "update_state": status["update_state"],
            "update_message": status["update_message"],
            "update_error": "",
            "models": [
                {
                    "name": name,
                    "size": 4 * 1024**3,
                    "modified_at": "2026-01-01T12:00:00Z",
                }
                for name in self.models
            ],
            "status_error": "モックの接続エラーです"
            if self.scenario == "error"
            else "",
            "ollama_settings": {
                **status,
                "context_length_label": f"{int(status['context_length']) // 1024}K",
                "keep_alive_label": status["keep_alive"],
                "flash_attention": {
                    "value": status["flash_attention"],
                    "label": "ON" if status["flash_attention"] == "1" else "OFF",
                },
            },
        }

    def ollama_action(self, data):
        action, model = data["action"], data.get("model", "")
        if action == "ollama_status":
            result = self.ollama
        elif action == "ollama_tags":
            result = {"models": self.ollama_detail()["models"]}
        elif action == "ollama_start":
            already_running = self.ollama["running"]
            self.ollama.update(running=True, api=True, job_ids=["12345"])
            for key in (
                "cpus",
                "memory",
                "parallel",
                "max_loaded_models",
                "context_length",
                "kv_cache_type",
                "keep_alive",
                "max_queue",
            ):
                if data.get(key):
                    self.ollama[key] = data[key]
            if data.get("flash_attention") is not None:
                self.ollama["flash_attention"] = "1" if data["flash_attention"] else "0"
            result = {"status": "already_running" if already_running else "started"}
        elif action == "ollama_stop":
            self.ollama.update(running=False, api=False, job_ids=[])
            result = {"status": "stopped"}
        elif action == "ollama_update_check":
            result = {
                "running_version": self.ollama["version"],
                "latest_version": self.ollama["latest_version"],
                "update_available": self.ollama["update_available"],
            }
        elif action == "ollama_update":
            self.ollama.update(
                version=self.ollama["latest_version"],
                update_available=False,
                update_state="completed",
                update_message="更新しました（モック）",
            )
            result = self.ollama
        elif action == "ollama_delete":
            if model in self.models:
                self.models.remove(model)
            result = {}
        elif action == "ollama_pull":
            if not model:
                raise ValueError("モデル名を入力してください")
            self.pull = {
                "state": "pulling",
                "model": model,
                "total": 100,
                "completed": 0,
                "started_at": time.monotonic(),
                "status": "ダウンロード中",
            }
            result = self.pull
        elif action == "ollama_pull_cancel":
            self.pull["state"] = "cancelled"
            result = self.pull
        elif action == "ollama_pull_status":
            if self.pull["state"] == "pulling":
                self.pull["completed"] = min(
                    100, int((time.monotonic() - self.pull["started_at"]) * 20)
                )
                if self.pull["completed"] == 100:
                    model = self.pull["model"]
                    if model not in self.models:
                        self.models.append(model)
                    self.pull.update(
                        state="completed",
                        status="完了",
                        litellm_registration={"state": "registered"},
                    )
            result = self.pull
        elif action == "ollama_register_model":
            result = {"state": "registered"}
        elif action == "ollama_sync_models":
            result = {
                "results": [
                    {"model": name, "state": "registered"} for name in self.models
                ]
            }
        else:
            raise ValueError("操作が不正です")
        return {"ok": True, "data": result}

    def issue_hub_token(self, username, data):
        token_id = self.next_id()
        token = SimpleNamespace(
            api_id=token_id,
            note=data.get("note", ""),
            scopes=data.get("scopes") or ["inherit"],
            created=datetime.now(timezone.utc).replace(tzinfo=None),
            last_activity=None,
            expires_at=None,
        )
        self.tokens[username][token_id] = token
        return {"id": token_id, "token": f"mock-hub-token-{token_id}"}
