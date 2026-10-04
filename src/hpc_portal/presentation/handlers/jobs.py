"""利用者のアプリ詳細・新規起動・状態のHTTP応答。"""

import asyncio
import secrets
import time

from jupyterhub.handlers.base import BaseHandler
from jupyterhub.utils import url_escape_path, url_path_join
from tornado import web

from hpc_portal.entrypoints.dependencies import get_dependencies
from hpc_portal.infrastructure.config.settings import (
    HPC_JOB_DNS_DOMAIN,
    HPC_OPENWEBUI_VERSION,
    HPC_PUBLIC_SCHEME,
)
from hpc_portal.infrastructure.jupyterhub.job_urls import _is_openwebui_spawner
from hpc_portal.infrastructure.linux.user_account_gateway import is_portal_admin
from hpc_portal.presentation.job_presenter import (
    openwebui_runtime_version,
    server_name_from_path,
    spawner_detail_context,
    user_memory_overuse,
)
from hpc_portal.presentation.ollama_presenter import shared_ollama_detail_context
from hpc_portal.presentation.schemas import HpcAppMemoryResponse


class HpcNewApplicationHandler(BaseHandler):
    """`/hub/new` を常に新規 named server の spawn 画面へ誘導する"""

    @web.authenticated
    async def get(self):
        """一意なnamed server名を生成して起動画面へ転送する。"""
        user = self.current_user
        user_name = user.escaped_name
        # 既存 server に吸われないよう、毎回ユニークな server 名を採番する
        server_name = f"app-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        target = url_path_join(
            self.hub.base_url,
            "spawn",
            url_escape_path(user_name),
            url_escape_path(server_name),
        )
        self.redirect(target)


class HpcAppDetailHandler(BaseHandler):
    """起動中アプリの詳細（割り当てリソース・停止・JUMP）"""

    @web.authenticated
    async def get(self, server_name_path):
        """起動中アプリの詳細画面を表示する。

        Args:
            server_name_path: URLに含まれるnamed server名。

        Raises:
            web.HTTPError: shared Ollamaを一般ユーザーが開いた場合。
        """
        user = self.current_user
        server_name = server_name_from_path(server_name_path)
        if server_name == "shared-ollama":
            if not is_portal_admin(user):
                raise web.HTTPError(403, "管理者のみアクセスできます")
            detail = shared_ollama_detail_context()
            html = await self.render_template(
                "app_detail.html",
                user=user,
                detail=detail,
                node_resources=get_dependencies().resources.execute(),
                hpc_public_scheme=HPC_PUBLIC_SCHEME,
                hpc_job_dns_domain=HPC_JOB_DNS_DOMAIN,
            )
            self.finish(html)
            return
        spawner = user.spawners.get(server_name)
        if spawner is None or not (
            getattr(spawner, "active", False) or getattr(spawner, "pending", None)
        ):
            self.redirect(url_path_join(self.hub.base_url, "home"))
            return
        detail = spawner_detail_context(spawner, server_name, user)
        try:
            html = await self.render_template(
                "app_detail.html",
                user=user,
                detail=detail,
                # template_vars の hpc_resource_snapshot（関数）と名前が衝突しないよう別名で渡す
                node_resources=get_dependencies().resources.execute(),
                hpc_public_scheme=HPC_PUBLIC_SCHEME,
                hpc_job_dns_domain=HPC_JOB_DNS_DOMAIN,
            )
        except Exception:
            self.log.exception("HPC: app_detail render failed for %s", server_name)
            raise
        self.finish(html)


class HpcOpenWebuiVersionHandler(BaseHandler):
    """ログインユーザー自身のOpen WebUIバージョンを返す。"""

    @web.authenticated
    async def get(self, server_name_path):
        """起動中と新規起動のOpen WebUIバージョンを返す。

        Args:
            server_name_path: URLに含まれるnamed server名。

        Raises:
            web.HTTPError: 対象が存在しない、またはOpen WebUIではない場合。
        """
        server_name = server_name_from_path(server_name_path)
        spawner = self.current_user.spawners.get(server_name)
        if spawner is None or not _is_openwebui_spawner(spawner):
            raise web.HTTPError(404, "Open WebUIが見つかりません")

        stored_version = (
            str(
                (getattr(spawner, "user_options", None) or {}).get(
                    "openwebui_version", ""
                )
            )
            .strip()
            .removeprefix("v")
        )
        runtime_version = None
        runtime_error = None
        verified = False
        if getattr(spawner, "active", False):
            runtime_version, runtime_error = await asyncio.to_thread(
                openwebui_runtime_version, spawner
            )
            verified = bool(runtime_version)
            if runtime_error:
                self.log.debug(
                    "HPC: Open WebUI version check failed for %s: %s",
                    server_name,
                    runtime_error,
                )
        displayed_version = runtime_version or stored_version or None
        target_version = HPC_OPENWEBUI_VERSION.removeprefix("v")
        self.set_header("Cache-Control", "no-store")
        self.write(
            {
                "ok": True,
                "data": {
                    "running_version": displayed_version,
                    "target_version": target_version,
                    "verified": verified,
                    "update_available": bool(
                        displayed_version
                        and target_version
                        and displayed_version != target_version
                    ),
                },
            }
        )


class HpcAppMemoryStatusHandler(BaseHandler):
    """ホーム画面のアプリカードを定期更新するための JSON API

    管理者の一覧は自動更新されるのに利用者のカードだけ再読み込みが要る、
    という差をなくすために用意する。GPU側は nvidia-smi が現在値を返すため
    実質リアルタイムに追随し、CPU側は JobAcctGatherFrequency=30 が下限となる。
    """

    @web.authenticated
    async def get(self):
        """ログインユーザーのアプリごとのメモリ使用状況を返す。"""
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate")
        usage = await asyncio.to_thread(user_memory_overuse, self.current_user)
        self.write(
            HpcAppMemoryResponse.model_validate(
                {"apps": usage, "updated_at": time.time()}
            ).model_dump()
        )
