"""Spawn中だけジョブ用OAuthホストを引き継ぐ。"""

import contextvars

_oauth_job_host_ctx = contextvars.ContextVar("hpc_oauth_job_host", default=None)
