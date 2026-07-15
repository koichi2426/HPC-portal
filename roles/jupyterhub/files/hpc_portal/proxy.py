"""configurable-http-proxyのルート検査と補修を提供する。"""

import asyncio
import time

from .common import (
    CHECK_ROUTES_DURATION_SECONDS,
    ConfigurableHTTPProxy,
    _one_at_a_time,
)
from .routing import (
    _hpc_chp_target_ready,
    _hpc_norm_routespec,
    _hpc_public_alias_routespec_for,
    _hpc_spawner_target_host,
    _sync_job_proxy_and_public,
)


class HpcConfigurableHTTPProxy(ConfigurableHTTPProxy):
    """job<SLURM_ID> に加え、Hub 公開ホストでも同一 singleuser へ転送するルートを維持する"""

    @_one_at_a_time
    async def check_routes(self, user_dict, service_dict, routes=None):
        """CHPルートを検査し、失敗時は標準実装へフォールバックする。

        Args:
            user_dict: JupyterHubユーザー辞書。
            service_dict: JupyterHubサービス辞書。
            routes: 取得済みルート。未指定時はCHPから取得する。
        """
        try:
            await self._hpc_check_routes_inner(user_dict, service_dict, routes)
        except Exception:
            self.log.exception(
                "HPC: check_routes failed; falling back to default implementation"
            )
            await super().check_routes(user_dict, service_dict, routes)

    async def _hpc_check_routes_inner(self, user_dict, service_dict, routes=None):
        """JOBIDサブドメインを含むCHPルートを同期する。

        Args:
            user_dict: JupyterHubユーザー辞書。
            service_dict: JupyterHubサービス辞書。
            routes: 取得済みルート。

        Returns:
            処理完了時はNone。
        """
        start = time.perf_counter()
        if not routes:
            self.log.debug("Fetching routes to check")
            routes = await self.get_all_routes()

        self.log.debug("Checking routes")

        def _raw_routespec_key(norm_s: str):
            """正規化済みroutespecに対応する元のキーを返す。

            Args:
                norm_s: 正規化済みroutespec。

            Returns:
                一致する元キー。存在しなければNone。
            """
            for k in routes:
                if _hpc_norm_routespec(k) == norm_s:
                    return k
            return None

        user_routes = set()
        for path, r in routes.items():
            if not isinstance(r, dict):
                continue
            d = r.get("data")
            if isinstance(d, dict) and "user" in d:
                user_routes.add(_hpc_norm_routespec(path))
        futures = []

        good_routes = {self.app.hub.routespec}

        hub = self.hub
        hub_key = _raw_routespec_key(_hpc_norm_routespec(self.app.hub.routespec))
        if hub_key is None:
            futures.append(self.add_hub_route(hub))
        else:
            route = routes[hub_key]
            if route["target"] != hub.host:
                self.log.warning(
                    "Updating Hub route %s → %s", route["target"], hub.host
                )
                futures.append(self.add_hub_route(hub))

        for user in user_dict.values():
            for name, spawner in user.spawners.items():
                # check_routes 評価直前に、現在の JOBID で routespec/alias を同期する
                _sync_job_proxy_and_public(spawner)
                _alias_tmp = _hpc_public_alias_routespec_for(spawner)
                if (
                    _alias_tmp
                    and getattr(spawner, "proxy_spec", None)
                    and _alias_tmp != spawner.proxy_spec
                ):
                    spawner._hpc_public_alias_routespec = _alias_tmp
                if spawner.ready:
                    spec = spawner.proxy_spec
                    if not spec:
                        self.log.warning(
                            "HPC: ready spawner %s has empty proxy_spec; skip",
                            spawner._log_name,
                        )
                        continue
                    good_routes.add(spec)
                    alias = getattr(spawner, "_hpc_public_alias_routespec", None)
                    if alias:
                        good_routes.add(alias)
                    spec_norm = _hpc_norm_routespec(spec)
                    if spec_norm not in user_routes:
                        self.log.warning(
                            "Adding missing route for %s (%s)", spec, spawner.server
                        )
                        await self.add_user(user, name)
                        routes = await self.get_all_routes()
                        user_routes = set()
                        for path, r in routes.items():
                            if not isinstance(r, dict):
                                continue
                            d = r.get("data")
                            if isinstance(d, dict) and "user" in d:
                                user_routes.add(_hpc_norm_routespec(path))
                        h = (
                            getattr(spawner.server, "host", None)
                            if spawner.server
                            else None
                        )
                        if (
                            alias
                            and _hpc_chp_target_ready(h)
                            and _raw_routespec_key(_hpc_norm_routespec(alias))
                            is None
                        ):
                            await self.add_route(
                                alias,
                                h,
                                {"user": user.name, "server_name": name},
                            )
                            self.log.info(
                                "HPC: CHP alias route %s → %s", alias, h
                            )
                            routes = await self.get_all_routes()
                    else:
                        rk = _raw_routespec_key(spec_norm)
                        if rk is None:
                            self.log.warning(
                                "HPC: CHP に %s が無いのに user_routes には存在; add_user で修復",
                                spec,
                            )
                            await self.add_user(user, name)
                            routes = await self.get_all_routes()
                            rk = _raw_routespec_key(spec_norm)
                        if rk is None:
                            continue
                        route = routes[rk]
                        if route["target"] != spawner.server.host:
                            self.log.warning(
                                "Updating route for %s (%s → %s)",
                                spec,
                                route["target"],
                                spawner.server,
                            )
                            await self.add_user(user, name)
                            routes = await self.get_all_routes()
                        h = (
                            getattr(spawner.server, "host", None)
                            if spawner.server
                            else None
                        )
                        if alias and spawner.server and _hpc_chp_target_ready(h):
                            ak = _raw_routespec_key(_hpc_norm_routespec(alias))
                            a_route = routes.get(ak) if ak else None
                            if not a_route or a_route.get("target") != h:
                                await self.add_route(
                                    alias,
                                    h,
                                    {"user": user.name, "server_name": name},
                                )
                                self.log.info(
                                    "HPC: CHP alias route (sync) %s → %s",
                                    alias,
                                    h,
                                )
                                routes = await self.get_all_routes()
                elif spawner.pending:
                    if spawner.proxy_spec:
                        good_routes.add(spawner.proxy_spec)
                    alias = getattr(spawner, "_hpc_public_alias_routespec", None)
                    if alias:
                        good_routes.add(alias)
                    h = _hpc_spawner_target_host(spawner)
                    if spawner.proxy_spec and _hpc_chp_target_ready(h):
                        sk = _raw_routespec_key(_hpc_norm_routespec(spawner.proxy_spec))
                        s_route = routes.get(sk) if sk else None
                        if not s_route or s_route.get("target") != h:
                            await self.add_route(
                                spawner.proxy_spec,
                                h,
                                {"user": user.name, "server_name": name},
                            )
                            self.log.info(
                                "HPC: CHP primary route (pending sync) %s → %s",
                                spawner.proxy_spec,
                                h,
                            )
                            routes = await self.get_all_routes()
                    if alias and _hpc_chp_target_ready(h):
                        ak = _raw_routespec_key(_hpc_norm_routespec(alias))
                        a_route = routes.get(ak) if ak else None
                        if not a_route or a_route.get("target") != h:
                            await self.add_route(
                                alias,
                                h,
                                {"user": user.name, "server_name": name},
                            )
                            self.log.info(
                                "HPC: CHP alias route (pending sync) %s → %s",
                                alias,
                                h,
                            )
                            routes = await self.get_all_routes()

        service_routes = {}
        for r in routes.values():
            if not isinstance(r, dict):
                continue
            d = r.get("data")
            if isinstance(d, dict) and "service" in d:
                service_routes[d["service"]] = r
        for service in service_dict.values():
            if service.server is None:
                continue
            good_routes.add(service.proxy_spec)
            if service.name not in service_routes:
                self.log.warning(
                    "Adding missing route for %s (%s)", service.name, service.server
                )
                futures.append(self.add_service(service))
            else:
                route = service_routes[service.name]
                if route["target"] != service.server.host:
                    self.log.warning(
                        "Updating route for %s (%s → %s)",
                        route.get("routespec", service.proxy_spec),
                        route["target"],
                        service.server.host,
                    )
                    futures.append(self.add_service(service))

        extra = getattr(self, "extra_routes", None) or {}
        for routespec, url in extra.items():
            good_routes.add(routespec)
            futures.append(self.add_route(routespec, url, {"extra": True}))

        good_routes_norm = {_hpc_norm_routespec(g) for g in good_routes}
        for routespec in routes:
            if _hpc_norm_routespec(routespec) not in good_routes_norm:
                self.log.warning("Deleting stale route %s", routespec)
                futures.append(self.delete_route(routespec))

        await asyncio.gather(*futures)
        stop = time.perf_counter()
        try:
            CHECK_ROUTES_DURATION_SECONDS.observe(stop - start)
        except Exception:
            pass
