"""JupyterHubのバージョン差を吸収する互換処理。"""

try:
    from jupyterhub._xsrf_utils import (
        _get_xsrf_token_cookie,
        _needs_check_xsrf,
        _set_xsrf_cookie,
    )
    from jupyterhub._xsrf_utils import check_xsrf_cookie as _jh_check_xsrf_cookie
except ImportError:
    from jupyterhub.handlers.base import _set_xsrf_cookie

    def _get_xsrf_token_cookie(handler):
        """互換対象のJupyterHubでXSRF Cookie未取得を表す。

        Args:
            handler: 対象のJupyterHub Handler。

        Returns:
            Cookie内のXSRF token情報。
        """
        return (None, None)

    def _needs_check_xsrf(handler):
        """互換対象のJupyterHubでは常にXSRF検証を要求する。

        Args:
            handler: 対象のJupyterHub Handler。

        Returns:
            XSRF検証が必要ならTrue。
        """
        return True

    def _jh_check_xsrf_cookie(handler):
        """Handler自身の実装を使ってXSRF Cookieを検証する。

        Args:
            handler: 対象のJupyterHub Handler。

        Returns:
            XSRF検証結果。
        """
        return handler.check_xsrf_cookie()


try:
    from jupyterhub.metrics import CHECK_ROUTES_DURATION_SECONDS
except Exception:  # noqa: S110

    class _DummyMetric:
        """メトリクスAPIがないJupyterHub向けの代替実装。"""

        def observe(self, _t):
            """計測値を受け取り、互換性維持のため何もしない。

            Args:
                _t: 観測対象の経過時間。
            """
            pass

    CHECK_ROUTES_DURATION_SECONDS = _DummyMetric()


try:
    from jupyterhub.proxy import _one_at_a_time
except Exception:  # noqa: S110

    def _one_at_a_time(method):
        """排他デコレーターがない場合に元のメソッドを返す。

        Args:
            method: デコレートするメソッド。

        Returns:
            排他制御を適用したメソッド。
        """
        return method


try:
    from jupyterhub.utils import subdomain_hook_idna as _default_subdomain_hook
except ImportError:
    from jupyterhub.utils import subdomain_hook_legacy as _default_subdomain_hook

__all__ = [
    "_get_xsrf_token_cookie",
    "_needs_check_xsrf",
    "_set_xsrf_cookie",
    "_jh_check_xsrf_cookie",
    "CHECK_ROUTES_DURATION_SECONDS",
    "_one_at_a_time",
    "_default_subdomain_hook",
]
