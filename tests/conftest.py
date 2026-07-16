"""HPCポータルの単体テストで共有する安全な設定を提供する。"""

import os

from traitlets.config import Config


# 実機の設定や秘密情報を読み込まず、import時検証を通すテスト専用値へ固定する。
os.environ.update(
    {
        "HPC_PUBLIC_DOMAIN": "portal.example.test",
        "HPC_JOB_DNS_DOMAIN": "jobs.example.test",
        "HPC_SLURM_NODE_NAME": "test-node",
        "HPC_PORTAL_ADMIN_USERS": '["admin"]',
        "HPC_PORTAL_PROTECTED_USERS": '["admin", "root"]',
        "HPC_PORTAL_SUDO_GROUP": "sudo",
        "HPC_OLLAMA_ALLOWED_CPUS": '["4", "8"]',
        "HPC_OLLAMA_ALLOWED_MEMORY": '["16G", "32G"]',
        "HPC_OLLAMA_DEFAULT_CPUS": "8",
        "HPC_OLLAMA_DEFAULT_MEMORY": "32G",
        "HPC_GPU_COUNT": "1",
        "HPC_LITELLM_INTERNAL_BASE_URL": "http://127.0.0.1:4000",
        "LITELLM_MASTER_KEY": "test-only-master-key",
        "HPC_SEARXNG_QUERY_URL": "http://127.0.0.1:8888/search?q=<query>",
        "OPENWEBUI_WEB_SEARCH_RESULT_COUNT": "3",
        "OPENWEBUI_WEB_SEARCH_CONCURRENT_REQUESTS": "1",
        "OPENWEBUI_WEB_LOADER_CONCURRENT_REQUESTS": "2",
        "OPENWEBUI_WEB_FETCH_MAX_CONTENT_LENGTH": "12000",
    }
)

# 本番ではjupyterhub_config.pyから渡されるConfigを、import副作用のない偽物で置き換える。
from hpc_portal import runtime  # noqa: E402

runtime.c = Config()
runtime.c.JupyterHub.template_vars = {}
runtime.c.JupyterHub.extra_handlers = []
