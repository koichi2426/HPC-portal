"""Slurm上でJupyterLabとOpen WebUIを起動するSpawnerを提供する。"""

from .apps import _is_openwebui_spawner, _job_host, _spawner_job_id
from .common import (
    HPC_PUBLIC_SCHEME,
    SlurmSpawner,
    _HPC_OPENWEBUI_KEY_LOCKS,
    _oauth_job_host_ctx,
    asyncio,
    re,
    subprocess,
    url_escape_path,
    url_path_join,
)
from .litellm import _hpc_litellm_get_openwebui_key
from .routing import (
    _hpc_chp_target_ready,
    _hpc_public_alias_routespec_for,
    _hpc_spawner_target_host,
    _hpc_wait_chp_target,
    _sync_job_proxy_and_public,
    _wait_for_tcp_port,
)


class HPCSlurmSpawner(SlurmSpawner):
    """Slurm JOBIDごとの公開URLとCHPルートを管理するSpawner。"""

    @property
    def hpc_failure_message(self) -> str:
        """直近の起動失敗をHome表示用の短いメッセージへ変換する。

        Returns:
            起動失敗がなければ空文字列、失敗していれば改行を除いた説明。
        """
        try:
            failure = getattr(self, "_failed", None)
        except asyncio.CancelledError:
            return "起動処理が中断されました"
        except Exception:
            return "起動状態を取得できませんでした"
        if not failure:
            return ""
        message = getattr(failure, "jupyterhub_message", "") or str(failure)
        message = " ".join(str(message or "").split())
        message = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-***", message)
        message = re.sub(r"\bBearer\s+\S+", "Bearer ***", message, flags=re.IGNORECASE)
        return message[:300] or "起動処理が完了しませんでした"

    def clear_state(self):
        """spawn 前に一時状態を初期化"""
        try:
            _oauth_job_host_ctx.set(None)
        except Exception:
            pass
        self._hpc_job_submitted = False
        self._hpc_submit_out = ""
        self._hpc_job_id = ""
        self._hpc_public_alias_routespec = None
        self._hpc_progress_message = "起動要求を受け付けました"
        self._hpc_progress_revision = 0
        super().clear_state()
        # ここでは sbatch しない。先行提出すると get_env が api_token 付与前に走り、
        # ジョブ内の JUPYTERHUB_API_TOKEN が空のまま固定され Hub の ready 判定が進まない。

    def _hpc_set_progress(self, message: str):
        """Spawn Pending画面へ表示する現在の起動工程を更新する。

        Args:
            message: 利用者へ表示する起動工程の説明。
        """
        message = str(message or "").strip()
        if not message or message == getattr(self, "_hpc_progress_message", ""):
            return
        self._hpc_progress_message = message
        self._hpc_progress_revision = (
            int(getattr(self, "_hpc_progress_revision", 0)) + 1
        )

    async def progress(self):
        """JupyterHub標準の起動待機画面へ工程メッセージを配信する。

        Yields:
            JupyterHub progress API形式のイベント辞書。実時間に基づく正確な
            割合は取得できないため、進捗率を付けず工程名だけを返す。
        """
        last_revision = -1
        last_message = None
        while self.pending:
            revision = int(getattr(self, "_hpc_progress_revision", 0))
            message = getattr(
                self,
                "_hpc_progress_message",
                "起動要求を処理しています",
            )
            job_id = str(getattr(self, "job_id", "") or "")
            if message.startswith("Slurmジョブ") and job_id:
                try:
                    if self.state_ispending():
                        message = f"Slurmジョブ {job_id} は実行待ちです"
                    elif self.state_isrunning():
                        message = (
                            f"Slurmジョブ {job_id} を実行中です。アプリの応答を待っています"
                        )
                except Exception:
                    # 状態判定に失敗しても、直前の具体的な工程メッセージは維持する。
                    pass
            if revision != last_revision or message != last_message:
                yield {"message": message}
                last_revision = revision
                last_message = message
            await asyncio.sleep(0.75)

    async def stop(self, now=False):
        """Slurm ジョブ停止後、同一ポートの Open WebUI 残骸を掃除して GPU/ポートを解放する"""
        port = getattr(self, "port", None) or 0
        app_choice = str((self.user_options or {}).get("app_choice", ""))
        user_name = self.user.name if getattr(self, "user", None) else ""
        try:
            return await super().stop(now)
        finally:
            if user_name and app_choice == "open-webui" and port:
                subprocess.run(
                    [
                        "pkill",
                        "-u",
                        user_name,
                        "-f",
                        f"open_webui.main:app --host 0.0.0.0 --port {port}",
                    ],
                    check=False,
                    timeout=5,
                )

    async def submit_batch_script(self):
        """Slurmジョブを一度だけ投入して初期CHPルートを登録する。

        Returns:
            BatchSpawnerが返したsbatch出力。
        """
        if getattr(self, "_hpc_job_submitted", False):
            if getattr(self, "_hpc_job_id", ""):
                self.job_id = self._hpc_job_id
            return getattr(self, "_hpc_submit_out", "") or ""
        self._hpc_set_progress("Slurmへ起動ジョブを投入しています")
        out = await super().submit_batch_script()
        self._hpc_job_submitted = True
        self._hpc_submit_out = out
        self._hpc_job_id = getattr(self, "job_id", "") or ""
        if self._hpc_job_id:
            self._hpc_set_progress(
                f"Slurmジョブ {self._hpc_job_id} の実行とアプリ応答を待っています"
            )
        else:
            self._hpc_set_progress("Slurmジョブの実行とアプリ応答を待っています")
        # super().start() 内の proxy 追加処理より前に job routespec を持たせる
        _sync_job_proxy_and_public(self)
        jid = _spawner_job_id(self)
        if jid:
            _oauth_job_host_ctx.set(_job_host(jid))
        # super().start() が ready 待ちで戻る前に詰まるケースに備え、
        # submit 時点で route を先行登録して spawn-pending から抜けられるようにする。
        proxy = (getattr(self.user, "settings", {}) or {}).get("proxy") if hasattr(self, "user") else None
        spec = str(getattr(self, "proxy_spec", "") or "")
        target = _hpc_spawner_target_host(self)
        if proxy is None:
            self.log.warning(
                "HPC: hub.proxy unavailable during submit sync (%s); defer to start/check_routes",
                self.name or "",
            )
            return out
        if not spec:
            self.log.warning(
                "HPC: empty proxy_spec during submit sync (%s); defer to start/check_routes",
                self.name or "",
            )
            return out
        if not _hpc_chp_target_ready(target):
            self.log.warning(
                "HPC: unresolved target during submit sync (%s -> %s); defer to start/check_routes",
                spec,
                target,
            )
            return out
        try:
            await proxy.add_route(
                spec,
                target,
                {"user": self.user.name, "server_name": self.name or ""},
            )
            self.log.info(
                "HPC: CHP primary route (submit sync) %s → %s",
                spec,
                target,
            )
            alias = _hpc_public_alias_routespec_for(self)
            if alias and alias != spec:
                self._hpc_public_alias_routespec = alias
                await proxy.add_route(
                    alias,
                    target,
                    {"user": self.user.name, "server_name": self.name or ""},
                )
                self.log.info(
                    "HPC: CHP alias route (submit sync) %s → %s",
                    alias,
                    target,
                )
        except Exception:
            self.log.exception(
                "HPC: failed submit-time route sync (%s); defer to start/check_routes",
                spec,
            )
        return out

    def get_env(self):
        """single-user serverへ公開URLとAPIトークンを渡す。

        Returns:
            JupyterHub標準値を拡張した環境変数辞書。
        """
        env = super().get_env()
        u = self.user
        if _is_openwebui_spawner(self):
            # OpenWebUI は job サブドメイン直下で公開
            jid = _spawner_job_id(self)
            if jid:
                pub = f"{HPC_PUBLIC_SCHEME}://{_job_host(jid)}/"
            else:
                pub = getattr(self, "public_url", "") or u.url
        elif self.name:
            pub = url_path_join(u.url, url_escape_path(self.name), "/")
        else:
            pub = u.url
        if not pub.endswith("/"):
            pub += "/"
        env["JUPYTERHUB_PUBLIC_URL"] = pub
        tok = (env.get("JUPYTERHUB_API_TOKEN") or "").strip() or (env.get("JPY_API_TOKEN") or "").strip()
        if getattr(self, "api_token", None):
            tok = tok or str(self.api_token).strip()
        if tok:
            env["JUPYTERHUB_API_TOKEN"] = tok
            env["JPY_API_TOKEN"] = tok
        return env

    def get_state(self):
        """動的ポートを含むSpawner状態を永続化する。

        Returns:
            JupyterHubが保存する状態辞書。
        """
        state = super().get_state()
        if getattr(self, "port", 0):
            state["_hpc_port"] = self.port
        return state

    def load_state(self, state):
        """保存済み状態から動的ポートと公開ルートを復元する。

        Args:
            state: JupyterHubが保存したSpawner状態。
        """
        super().load_state(state)
        if "_hpc_port" in state:
            self.port = state["_hpc_port"]
        if getattr(self, "job_id", None) and self.job_id:
            _sync_job_proxy_and_public(self)
            al = _hpc_public_alias_routespec_for(self)
            if al and al != getattr(self, "proxy_spec", None):
                self._hpc_public_alias_routespec = al

    async def start(self):
        """利用者別認可を確認してSlurm上のアプリを起動する。

        Returns:
            JupyterHubがCHPへ登録する接続先URL。

        Raises:
            RuntimeError: Open WebUI Key、ポート、起動待機に失敗した場合。
        """
        try:
            self._hpc_set_progress("起動設定を確認しています")
            if _is_openwebui_spawner(self):
                # Open WebUI には、ユーザー専用の永続 Virtual Key を渡す。
                self._hpc_set_progress("Open WebUIの利用権限を確認しています")
                username = self.user.name if getattr(self, "user", None) else ""
                if not username:
                    raise RuntimeError("Open WebUI 用のユーザー情報を取得できません")
                for other in (getattr(self.user, "spawners", {}) or {}).values():
                    if other is self or not _is_openwebui_spawner(other):
                        continue
                    if getattr(other, "active", False) or getattr(other, "pending", None):
                        raise RuntimeError(
                            "Open WebUIはユーザーごとに1つだけ起動できます。"
                            "起動中のOpen WebUIを停止してから再試行してください"
                        )
                lock = _HPC_OPENWEBUI_KEY_LOCKS.setdefault(username, asyncio.Lock())
                async with lock:
                    key, err = _hpc_litellm_get_openwebui_key(username)
                if err:
                    raise RuntimeError(f"Open WebUI を起動できません: {err}")
                self.environment = {
                    **dict(getattr(self, "environment", {}) or {}),
                    "OPENWEBUI_LITELLM_API_KEY": key or "",
                }
                await self.submit_batch_script()
                p = getattr(self, "port", 0) or 0
                try:
                    p = int(p)
                except Exception:
                    p = 0
                if p <= 0:
                    jid = _spawner_job_id(self)
                    if jid.isdigit():
                        p = 20000 + (int(jid) % 20000)
                if p <= 0:
                    raise RuntimeError(
                        f"HPC: unresolved openwebui port for {self.name or ''}"
                    )
                self.port = p
                self.ip = "127.0.0.1"
                self._hpc_set_progress("Open WebUIの応答を待っています")
                if not await _wait_for_tcp_port(self.ip, int(self.port), timeout=120.0):
                    raise RuntimeError(
                        f"HPC: OpenWebUI TCP not reachable on {self.ip}:{self.port}"
                    )
                self._hpc_set_progress("接続経路を準備しています")
                _sync_job_proxy_and_public(self)
                proxy = (getattr(self.user, "settings", {}) or {}).get("proxy") if hasattr(self, "user") else None
                target = f"http://{self.ip}:{self.port}"
                spec = str(getattr(self, "proxy_spec", "") or "")
                if proxy is None:
                    self.log.warning(
                        "HPC: hub.proxy unavailable during openwebui start (%s); defer to check_routes",
                        self.name or "",
                    )
                elif not spec:
                    self.log.warning(
                        "HPC: empty proxy_spec during openwebui start (%s); defer to check_routes",
                        self.name or "",
                    )
                else:
                    try:
                        await proxy.add_route(
                            spec,
                            target,
                            {"user": self.user.name, "server_name": self.name or ""},
                        )
                        self.log.info(
                            "HPC: CHP primary route (openwebui ready) %s → %s",
                            spec,
                            target,
                        )
                        alias = _hpc_public_alias_routespec_for(self)
                        if alias and alias != spec:
                            self._hpc_public_alias_routespec = alias
                            await proxy.add_route(
                                alias,
                                target,
                                {"user": self.user.name, "server_name": self.name or ""},
                            )
                            self.log.info(
                                "HPC: CHP alias route (openwebui ready) %s → %s",
                                alias,
                                target,
                            )
                    except Exception:
                        self.log.exception(
                            "HPC: failed to add route during openwebui start; defer to check_routes"
                        )
                if spec and not getattr(self, "_hpc_public_alias_routespec", None):
                    alias = _hpc_public_alias_routespec_for(self)
                    if alias and alias != spec:
                        self._hpc_public_alias_routespec = alias
                self._hpc_set_progress("Open WebUIを利用できます")
                return (self.ip, self.port)

            self._hpc_set_progress("Slurmへ起動ジョブを投入しています")
            ret = await super().start()
            self._hpc_set_progress("接続経路を準備しています")
            _sync_job_proxy_and_public(self)
            srv = getattr(self, "server", None)
            h = getattr(srv, "host", None) if srv else None
            if not _hpc_chp_target_ready(h):
                h = await _hpc_wait_chp_target(self)
            if not _hpc_chp_target_ready(h):
                h = _hpc_spawner_target_host(self)
            proxy = None
            try:
                proxy = (getattr(self.user, "settings", {}) or {}).get("proxy")
            except Exception:
                proxy = None
            if proxy is None:
                try:
                    proxy = (getattr(self, "settings", {}) or {}).get("proxy")
                except Exception:
                    proxy = None
            if proxy is None:
                try:
                    proxy = getattr(getattr(self, "hub", None), "proxy", None)
                except Exception:
                    proxy = None
            if proxy is None:
                self.log.warning(
                    "HPC: proxy object unavailable during start (%s)",
                    getattr(self, "name", ""),
                )
            if (
                proxy is not None
                and getattr(self, "proxy_spec", None)
                and _hpc_chp_target_ready(h)
            ):
                try:
                    routes = await proxy.get_all_routes()
                    spec = str(self.proxy_spec)
                    spec_key = spec if spec.startswith("/") else f"/{spec}"
                    route = routes.get(spec) or routes.get(spec_key)
                    if not route or route.get("target") != h:
                        await proxy.add_route(
                            self.proxy_spec,
                            h,
                            {"user": self.user.name, "server_name": self.name or ""},
                        )
                        self.log.info(
                            "HPC: CHP primary route (start) %s → %s",
                            self.proxy_spec,
                            h,
                        )
                except Exception:
                    self.log.exception(
                        "HPC: failed to ensure primary route during start (%s)",
                        self.proxy_spec,
                    )
            alias = _hpc_public_alias_routespec_for(self)
            if (
                alias
                and getattr(self, "proxy_spec", None)
                and alias != self.proxy_spec
            ):
                self._hpc_public_alias_routespec = alias
                # 起動直後に alias ルートが無いと /user/.../oauth_callback が Hub 側へ戻されて
                # Redirect loop になるため、有効な host:port が確定していれば即時に登録する。
                if not _hpc_chp_target_ready(h):
                    h = await _hpc_wait_chp_target(self)
                if not _hpc_chp_target_ready(h):
                    h = _hpc_spawner_target_host(self)
                if not _hpc_chp_target_ready(h):
                    # host が :0 のままでも、CHP に既に本ルートがあれば target を再利用して alias を張る
                    try:
                        if proxy is not None and getattr(self, "proxy_spec", None):
                            routes = await proxy.get_all_routes()
                            k1 = self.proxy_spec
                            k2 = f"/{k1}" if not str(k1).startswith("/") else str(k1)
                            r = routes.get(k1) or routes.get(k2) or {}
                            t = r.get("target")
                            if _hpc_chp_target_ready(t):
                                h = t
                                self.log.info(
                                    "HPC: resolved alias target from CHP routes %s -> %s",
                                    self.proxy_spec,
                                    h,
                                )
                    except Exception:
                        self.log.exception(
                            "HPC: failed to resolve alias target from existing CHP routes"
                        )
                if not _hpc_chp_target_ready(h):
                    h = _hpc_spawner_target_host(self)
                if _hpc_chp_target_ready(h):
                    try:
                        proxy = self.user.settings.get("proxy")
                        if proxy is not None:
                            await proxy.add_route(
                                alias,
                                h,
                                {"user": self.user.name, "server_name": self.name or ""},
                            )
                            self.log.info("HPC: CHP alias route (start) %s → %s", alias, h)
                    except Exception:
                        self.log.exception(
                            "HPC: failed to add alias route during start (%s)", alias
                        )
                else:
                    self.log.warning(
                        "HPC: alias route defer until check_routes (host not ready): %s",
                        h,
                    )
            # ready 判定前でも route が抜けるケースに備えて、短い遅延後に再同期を1回実施
            # （OpenWebUI の起動タイミングで CHP 登録が取りこぼされるのを防ぐ）
            if proxy is not None and _hpc_chp_target_ready(h):
                try:
                    await asyncio.sleep(0.5)
                    routes = await proxy.get_all_routes()
                    spec = str(getattr(self, "proxy_spec", "") or "")
                    if spec:
                        spec_key = spec if spec.startswith("/") else f"/{spec}"
                        r = routes.get(spec) or routes.get(spec_key)
                        if not r or r.get("target") != h:
                            await proxy.add_route(
                                spec,
                                h,
                                {"user": self.user.name, "server_name": self.name or ""},
                            )
                            self.log.info(
                                "HPC: CHP primary route (post-start sync) %s → %s",
                                spec,
                                h,
                            )
                    alias2 = getattr(self, "_hpc_public_alias_routespec", None)
                    if alias2:
                        ak = alias2 if str(alias2).startswith("/") else f"/{alias2}"
                        ar = routes.get(alias2) or routes.get(ak)
                        if not ar or ar.get("target") != h:
                            await proxy.add_route(
                                alias2,
                                h,
                                {"user": self.user.name, "server_name": self.name or ""},
                            )
                            self.log.info(
                                "HPC: CHP alias route (post-start sync) %s → %s",
                                alias2,
                                h,
                            )
                except Exception:
                    self.log.exception(
                        "HPC: failed post-start route sync (%s)",
                        getattr(self, "proxy_spec", ""),
                    )
            self._hpc_set_progress("アプリを利用できます")
            return ret
        finally:
            try:
                _oauth_job_host_ctx.set(None)
            except Exception:
                pass
