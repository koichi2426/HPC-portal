"""JupyterHubのログイン、ログアウト、Cookie処理を拡張する。"""

from jupyterhub.handlers.base import BaseHandler
from jupyterhub.handlers.login import LoginHandler, LogoutHandler
from tornado import web
from tornado.httputil import url_concat

from hpc_portal.infrastructure.config.settings import (
    HPC_JOB_DNS_DOMAIN,
)
from hpc_portal.infrastructure.jupyterhub.framework_compatibility import (
    _get_xsrf_token_cookie,
    _jh_check_xsrf_cookie,
    _needs_check_xsrf,
    _set_xsrf_cookie,
)


def clear_domain_auth_cookies(handler):
    """共有ドメインに設定された認証Cookieを削除する。

    Args:
        handler: Cookie操作を行うJupyterHubハンドラ。
    """
    cookie_opts = dict(handler.settings.get("cookie_options") or {})
    domains = [f".{HPC_JOB_DNS_DOMAIN}", HPC_JOB_DNS_DOMAIN, None]
    paths = {handler.hub.base_url, handler.base_url, "/"}
    names = {
        handler.hub.cookie_name,
        "jupyterhub-session-id",
        "_xsrf",
        "jupyterhub-services",
    }
    for path in paths:
        for cookie_name in names:
            for domain in domains:
                kwargs = {"path": path, "httponly": True}
                if cookie_opts.get("secure"):
                    kwargs["secure"] = True
                if cookie_opts.get("samesite"):
                    kwargs["samesite"] = cookie_opts["samesite"]
                if domain:
                    kwargs["domain"] = domain
                handler.clear_cookie(cookie_name, **kwargs)


_hpc_original_clear_login_cookie = BaseHandler.clear_login_cookie


def clear_login_cookie(self, name=None):
    """標準処理に加えて共有ドメインのCookieを削除する。

    Args:
        name: 個別に削除するCookie名。
    """
    _hpc_original_clear_login_cookie(self, name=name)
    clear_domain_auth_cookies(self)


_hpc_original_default_handle_logout = LogoutHandler.default_handle_logout


async def default_handle_logout(self):
    """標準ログアウト後に共有ドメインのCookieを削除する。

    Args:
        self: LogoutHandlerインスタンス。
    """
    await _hpc_original_default_handle_logout(self)
    clear_domain_auth_cookies(self)


_hpc_original_render_logout_page = LogoutHandler.render_logout_page


async def render_logout_page(self):
    """ログアウト後に再ログイン画面へ遷移させる。

    Args:
        self: LogoutHandlerインスタンス。

    Returns:
        自動ログイン時は標準ハンドラの戻り値。
    """
    if self.authenticator.auto_login:
        return await _hpc_original_render_logout_page(self)
    self.redirect(
        url_concat(self.settings["login_url"], {"logout": "1"}),
        permanent=False,
    )


_hpc_original_login_get = LoginHandler.get


async def login_get(self):
    """ログアウト直後の古いセッションを除去してログイン画面を表示する。

    Args:
        self: LoginHandlerインスタンス。
    """
    # ログアウト直後は残存 cookie を信用せず、再ログイン画面を必ず出す
    if self.get_argument("logout", None) is not None:
        self._jupyterhub_user = None
        self.clear_login_cookie()
        if hasattr(self, "_xsrf_token"):
            del self._xsrf_token
        if hasattr(self, "_session_id"):
            del self._session_id
        self.set_session_cookie()
        username = self.get_argument("username", default="")
        if self.request.headers.get("Sec-Fetch-Mode", "navigate") == "navigate":
            _set_xsrf_cookie(
                self,
                self._xsrf_token_id,
                cookie_path=self.hub.base_url,
                xsrf_token=self.xsrf_token,
            )
        self.finish(await self._render(username=username))
        return
    if not self.current_user and not self.get_session_cookie():
        self.set_session_cookie()
        if hasattr(self, "_xsrf_token"):
            del self._xsrf_token
    await _hpc_original_login_get(self)


def login_xsrf_token_id(self):
    """ログインセッションに固定したXSRFトークンIDを返す。

    Args:
        self: LoginHandlerインスタンス。

    Returns:
        セッションIDを基にしたバイト列。
    """
    # ログイン画面では IP 由来の anonymous id を使わない（Cloudflare 経由で変動する）。
    # current_user も信用しない（ログアウト直後に古い hub cookie が残ることがある）。
    session_id = self.get_session_cookie() or ""
    return f"{session_id}:".encode("utf-8")


_hpc_original_login_check_xsrf = LoginHandler.check_xsrf_cookie


def login_check_xsrf(self):
    """Cloudflare経由のログインフォームに対してXSRFを検証する。

    Args:
        self: LoginHandlerインスタンス。

    Returns:
        検証成功時はNoneまたは標準検証処理の戻り値。

    Raises:
        web.HTTPError: トークン不足または不一致の場合。
    """
    if not _needs_check_xsrf(self):
        return None
    token = (
        self.get_argument("_xsrf", None)
        or self.request.headers.get("X-Xsrftoken")
        or self.request.headers.get("X-Csrftoken")
    )
    if not token:
        raise web.HTTPError(403, f"'_xsrf' argument missing from {self.request.method}")
    try:
        token_b = token.encode("utf8")
    except UnicodeEncodeError:
        raise web.HTTPError(403, "'_xsrf' argument invalid")
    cookie_token, _cookie_id = _get_xsrf_token_cookie(self)
    if cookie_token and token_b == cookie_token:
        self._xsrf_token = cookie_token
        return None
    try:
        return _jh_check_xsrf_cookie(self)
    except web.HTTPError as e:
        self.log.error("XSRF error on login form: %s", e)
        if self.request.headers.get("Sec-Fetch-Mode", "navigate") == "navigate":
            raise web.HTTPError(
                e.status_code, "Login form invalid or expired. Try again."
            ) from e
        raise


_installed = False


def install_session_hooks():
    """ログイン・ログアウト・XSRF処理を、共有ドメイン対応の実装へ差し替える。"""
    global _installed
    if _installed:
        return
    BaseHandler.clear_login_cookie = clear_login_cookie
    LogoutHandler.default_handle_logout = default_handle_logout
    LogoutHandler.render_logout_page = render_logout_page
    LoginHandler.get = login_get
    LoginHandler._xsrf_token_id = property(login_xsrf_token_id)
    LoginHandler.check_xsrf_cookie = login_check_xsrf
    _installed = True
