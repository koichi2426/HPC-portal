"""Spawner設定とApptainer用Slurmバッチスクリプトを登録する。"""

from .common import (
    HPC_BATCH_EXECHOST_EXP,
    HPC_JOB_DNS_DOMAIN,
    HPC_PUBLIC_SCHEME,
    HPC_SEARXNG_QUERY_URL,
    OPENWEBUI_WEB_FETCH_MAX_CONTENT_LENGTH,
    OPENWEBUI_WEB_LOADER_CONCURRENT_REQUESTS,
    OPENWEBUI_LITELLM_BASE_URL,
    OPENWEBUI_WEB_SEARCH_CONCURRENT_REQUESTS,
    OPENWEBUI_WEB_SEARCH_RESULT_COUNT,
    Proxy,
    c,
    url_escape_path,
    url_path_join,
)
from .forms import apply_user_options, make_options_form, options_from_form
from .proxy import HpcConfigurableHTTPProxy
from .spawner import HPCSlurmSpawner


# 4. Spawner / Hub クラス紐付け
c.JupyterHub.proxy_class = HpcConfigurableHTTPProxy
c.JupyterHub.spawner_class = HPCSlurmSpawner
c.HPCSlurmSpawner.options_form = make_options_form
c.HPCSlurmSpawner.options_from_form = options_from_form
# batchspawner はジョブ RUNNING 後に self.ip = state_gethost() で上書きする（c.Spawner.ip は最終的に使われない）。
# Slurm が返す短いノード名（例: gx10-ac12）を CHP の target にすると、Node からの外向き HTTP が
# 名前解決・ヘアピン等で失敗し Cloudflare 経由で 502 になることがある。
# Hub・CHP・ジョブが同一ホスト（group_vars の localhost 運用）では 127.0.0.1 に固定する。
# 計算ノードが分かれる場合は FQDN や MultiSlurmSpawner.daemon_resolver 等に切り替えること。
c.HPCSlurmSpawner.state_exechost_exp = HPC_BATCH_EXECHOST_EXP
c.HPCSlurmSpawner.ip = "127.0.0.1"
c.HPCSlurmSpawner.req_nprocs = "2"
c.HPCSlurmSpawner.req_memory = "4G"
c.HPCSlurmSpawner.req_runtime = "02:00:00"
c.HPCSlurmSpawner.req_partition = "debug"
c.Spawner.start_timeout = 300
c.Spawner.cmd = ["jupyterhub-singleuser"]
c.Spawner.environment = {
    "OPENWEBUI_LITELLM_BASE_URL": OPENWEBUI_LITELLM_BASE_URL,
}
c.Spawner.apply_user_options = apply_user_options

# 5. 起動スクリプト (Apptainer版・多重エスケープ回避)
# BatchSpawner は Jinja で展開するため、変数は Jinja 方式、シェルの波括弧は通常の一組にする。
js = "{" + "{"
je = "}" + "}"
lbrace = "{"
rbrace = "}"

# CHP の削除 API は既定で user.proxy_spec（ユーザー名サブドメイン）を使うため、
# job<JOBID>.<domain> に差し替えた spawner.proxy_spec を確実に削除する
_orig_proxy_delete_user = Proxy.delete_user


async def _hpc_proxy_delete_user(self, user, server_name="", client=None):
    """停止したserverのCHP主ルートと公開aliasを削除する。

    Args:
        self: JupyterHub Proxyインスタンス。
        user: 対象のJupyterHubユーザー。
        server_name: named server名。
        client: 互換性維持用の未使用引数。
    """
    sp = user.spawners.get(server_name)
    if sp is not None:
        alias = getattr(sp, "_hpc_public_alias_routespec", None)
        if alias:
            self.log.info("Removing user %s CHP public-alias (%s)", user.name, alias)
            try:
                await self.delete_route(alias)
            except Exception:
                self.log.exception("HPC: failed to delete alias route %s", alias)
    if sp is not None and getattr(sp, "proxy_spec", None):
        routespec = sp.proxy_spec
    else:
        routespec = user.proxy_spec
        if server_name:
            routespec = url_path_join(user.proxy_spec, url_escape_path(server_name), "/")
    self.log.info("Removing user %s from proxy (%s)", user.name, routespec)
    await self.delete_route(routespec)


Proxy.delete_user = _hpc_proxy_delete_user

OPENWEBUI_DEFAULT_MODEL_METADATA_JSON = (
    '{"capabilities":{"file_context":true,"web_search":true,'
    '"image_generation":false,"code_interpreter":true,"terminal":false,'
    '"builtin_tools":true},"builtinTools":{"time":true,"memory":true,'
    '"notes":true,"chats":false,"knowledge":false,"channels":false,'
    '"web_search":true,"image_generation":false,"code_interpreter":true,'
    '"tasks":false,"automations":false,"calendar":false},'
    '"defaultFeatureIds":["web_search","code_interpreter"]}'
)
OPENWEBUI_DEFAULT_MODEL_PARAMS_JSON = '{"think":false,"function_calling":"native"}'


c.HPCSlurmSpawner.batch_script = f"""#!/bin/bash
#SBATCH --job-name={js} job_name {je}
#SBATCH --partition={js} partition {je}
#SBATCH --cpus-per-task={js} nprocs {je}
#SBATCH --mem={js} memory {je}
{js} runtime_line {je}
#SBATCH --chdir={js} homedir {je}
#SBATCH --output={js} homedir {je}/.jupyterhub-slurm-%j.log
{js} gres_line {je}

set -euo pipefail
export PATH=/opt/jupyterhub/venv/bin:$PATH

# 環境によっては JUPYTERHUB_API_TOKEN が空で JPY_API_TOKEN のみ渡るため補完する
_hub_token="$(printenv JUPYTERHUB_API_TOKEN || true)"
_jpy_token="$(printenv JPY_API_TOKEN || true)"
if [ -z "$_hub_token" ] && [ -n "$_jpy_token" ]; then
    export JUPYTERHUB_API_TOKEN="$_jpy_token"
fi

# GPU/CUDA: ホストの CUDA Toolkit (nvcc) をコンテナへバインドし、ランタイムのライブラリパスを整える
# set -u 下でも printenv で未設定変数を安全に読む（batch_script 内に二重波括弧を入れると壊れる）
_hpc_cuda_home=""
_hpc_nvcc_path=""
_cuda_from_env="$(printenv CUDA_HOME 2>/dev/null || true)"
if [ -n "$_cuda_from_env" ] && [ -x "$_cuda_from_env/bin/nvcc" ]; then
    _hpc_cuda_home="$_cuda_from_env"
    _hpc_nvcc_path="$_cuda_from_env/bin/nvcc"
else
    for _d in /usr/local/cuda /usr/local/cuda-13.0 /usr/local/cuda-13 /usr/local/cuda-12.6 /usr/local/cuda-12.4; do
        if [ -x "$_d/bin/nvcc" ]; then
            _hpc_cuda_home="$_d"
            _hpc_nvcc_path="$_d/bin/nvcc"
            break
        fi
    done
fi
if [ -z "$_hpc_nvcc_path" ] && command -v nvcc >/dev/null 2>&1; then
    _hpc_nvcc_path="$(command -v nvcc)"
fi
HPC_APPTAINER_BIND=""
if [ -n "$_hpc_cuda_home" ]; then
    export CUDA_HOME="$_hpc_cuda_home"
    export PATH="$CUDA_HOME/bin:$PATH"
    HPC_APPTAINER_BIND="-B $CUDA_HOME:$CUDA_HOME"
elif [ -n "$_hpc_nvcc_path" ]; then
    export PATH="$(dirname "$_hpc_nvcc_path"):$PATH"
    HPC_APPTAINER_BIND="-B $_hpc_nvcc_path:$_hpc_nvcc_path"
    for _libdir in /usr/lib/cuda /usr/lib/nvidia-cuda-toolkit; do
        if [ -d "$_libdir" ]; then
            HPC_APPTAINER_BIND="$HPC_APPTAINER_BIND -B $_libdir:$_libdir"
        fi
    done
fi
_hpc_ld_path="/usr/lib/aarch64-linux-gnu:/.singularity.d/libs"
_existing_ld="$(printenv LD_LIBRARY_PATH 2>/dev/null || true)"
if [ -n "$_existing_ld" ]; then
    _hpc_ld_path="$_hpc_ld_path:$_existing_ld"
fi
export LD_LIBRARY_PATH="$_hpc_ld_path"
if [ -n "$_hpc_cuda_home" ]; then
    for _libdir in "$CUDA_HOME/lib64" "$CUDA_HOME/targets/aarch64-linux/lib"; do
        if [ -d "$_libdir" ]; then
            export LD_LIBRARY_PATH="$_libdir:$LD_LIBRARY_PATH"
        fi
    done
elif [ -n "$_hpc_nvcc_path" ]; then
    for _libdir in /usr/lib/cuda/lib64 /usr/lib/cuda/targets/aarch64-linux/lib; do
        if [ -d "$_libdir" ]; then
            export LD_LIBRARY_PATH="$_libdir:$LD_LIBRARY_PATH"
        fi
    done
fi

APP_CHOICE="{js} app_choice {je}"
if [ "$APP_CHOICE" = "open-webui" ]; then
    OPENWEBUI_IMAGE="/opt/images/openwebui.sif"
    SERVICE_PORT="$(printenv JUPYTERHUB_SERVICE_PORT || true)"
    SERVICE_URL="$(printenv JUPYTERHUB_SERVICE_URL || true)"
    CMDLINE="{js} cmd {je}"
    PORT="$SERVICE_PORT"
    case "$PORT" in
      ''|*[!0-9]*|0) PORT="" ;;
    esac
    if [ -z "$PORT" ]; then
      PORT="$(printf '%s' "$SERVICE_URL" | sed -n 's#.*:\\([0-9]\\+\\)/.*#\\1#p')"
      if [ "$PORT" = "0" ]; then
        PORT=""
      fi
    fi
    if [ -z "$PORT" ]; then
      PORT="$(printf '%s' "$CMDLINE" | sed -n 's/.*--port=\\([0-9]\\+\\).*/\\1/p')"
    fi
    if [ -z "$PORT" ]; then
      # 環境によっては起動直後の service_url が :0 のまま来るため、JOBID からフォールバックポートを決める
      PORT="$((20000 + ($(printenv SLURM_JOB_ID || echo 0) % 20000)))"
      echo "dynamic port unavailable, fallback to PORT=$PORT (service_port=$SERVICE_PORT service_url=$SERVICE_URL)" >&2
    fi
    port_is_listening() {lbrace}
      # ss の式フィルタは環境によって解釈差があるため、標準の一覧出力を awk で判定する。
      # Local Address の末尾が :<port> の LISTEN socket だけを対象にする。
      ss -H -ltn | awk -v port="$1" '$4 ~ (":" port "$") {lbrace} found=1; exit {rbrace} END {lbrace} exit !found {rbrace}'
    {rbrace}
    # 以前の失敗ジョブの残骸で同一ポートが占有されると OpenWebUI が即終了するため、
    # 同ユーザーの同一ポート open_webui プロセスを掃除してから起動する。
    if port_is_listening "$PORT"; then
      echo "PORT $PORT already in use; cleaning stale open_webui process" >&2
      pkill -u "$USER" -f "open_webui.main:app --host 0.0.0.0 --port $PORT" || true
      sleep 1
    fi
    if port_is_listening "$PORT"; then
      echo "PORT $PORT still busy after cleanup; abort startup to avoid immediate crash" >&2
      exit 1
    fi
    # 会話履歴・設定・モデルキャッシュはnamed server名に依存させず、ユーザー単位で再利用する。
    OPENWEBUI_DATA_DIR="$HOME/.local/share/open-webui"
    HF_HOME_DIR="$OPENWEBUI_DATA_DIR/hf"
    HF_HUB_CACHE_DIR="$HF_HOME_DIR/hub"
    SENTENCE_TRANSFORMERS_HOME_DIR="$OPENWEBUI_DATA_DIR/sentence-transformers"
    TRANSFORMERS_CACHE_DIR="$HF_HOME_DIR/transformers"
    LITELLM_BASE_URL="$(printenv OPENWEBUI_LITELLM_BASE_URL || true)"
    LITELLM_API_KEY="$(printenv OPENWEBUI_LITELLM_API_KEY || true)"
    OPENWEBUI_DEFAULT_MODEL_METADATA='{OPENWEBUI_DEFAULT_MODEL_METADATA_JSON}'
    OPENWEBUI_DEFAULT_MODEL_PARAMS='{OPENWEBUI_DEFAULT_MODEL_PARAMS_JSON}'
    mkdir -p "$OPENWEBUI_DATA_DIR/static" "$HOME/.ollama" "$HF_HUB_CACHE_DIR" "$SENTENCE_TRANSFORMERS_HOME_DIR" "$TRANSFORMERS_CACHE_DIR"
    # 同じSQLite DBを複数プロセスで開かないよう、ユーザー単位のデータ領域を排他する。
    OPENWEBUI_LOCK_FILE="$OPENWEBUI_DATA_DIR/.instance.lock"
    if ! flock -n "$OPENWEBUI_LOCK_FILE" true; then
      echo "Open WebUI is already running for this user" >&2
      exit 1
    fi
    JOB_HOST="job$(printenv SLURM_JOB_ID || echo 0).{HPC_JOB_DNS_DOMAIN}"
    WEBUI_EXTERNAL_URL="{HPC_PUBLIC_SCHEME}://$JOB_HOST/"
    exec flock -n "$OPENWEBUI_LOCK_FILE" apptainer exec --nv $HPC_APPTAINER_BIND "$OPENWEBUI_IMAGE" env \
      "HOST=0.0.0.0" \
      "PORT=$PORT" \
      "DATA_DIR=$OPENWEBUI_DATA_DIR" \
      "STATIC_DIR=$OPENWEBUI_DATA_DIR/static" \
      "WEBUI_AUTH=False" \
      "ENABLE_OPENAI_API=True" \
      "OPENAI_API_BASE_URL=$LITELLM_BASE_URL" \
      "OPENAI_API_KEY=$LITELLM_API_KEY" \
      "ENABLE_OLLAMA_API=False" \
      "ENABLE_DIRECT_CONNECTIONS=False" \
      "ENABLE_MEMORIES=True" \
      "ENABLE_MEMORY_SYSTEM_CONTEXT=False" \
      "ENABLE_NOTES=True" \
      "ENABLE_WEB_SEARCH=True" \
      "WEB_SEARCH_ENGINE=searxng" \
      "SEARXNG_QUERY_URL={HPC_SEARXNG_QUERY_URL}" \
      "SEARXNG_LANGUAGE=all" \
      "WEB_SEARCH_RESULT_COUNT={OPENWEBUI_WEB_SEARCH_RESULT_COUNT}" \
      "WEB_SEARCH_CONCURRENT_REQUESTS={OPENWEBUI_WEB_SEARCH_CONCURRENT_REQUESTS}" \
      "WEB_LOADER_CONCURRENT_REQUESTS={OPENWEBUI_WEB_LOADER_CONCURRENT_REQUESTS}" \
      "WEB_FETCH_MAX_CONTENT_LENGTH={OPENWEBUI_WEB_FETCH_MAX_CONTENT_LENGTH}" \
      "BYPASS_WEB_SEARCH_WEB_LOADER=False" \
      "ENABLE_RAG_LOCAL_WEB_FETCH=False" \
      "ENABLE_WEB_SEARCH_CONFIRMATION=False" \
      "ENABLE_IMAGE_GENERATION=False" \
      "ENABLE_CODE_INTERPRETER=True" \
      "ENABLE_CHANNELS=False" \
      "ENABLE_CALENDAR=False" \
      "ENABLE_AUTOMATIONS=False" \
      "ENABLE_FOLDERS=True" \
      "DEFAULT_LOCALE=ja-JP" \
      "DEFAULT_MODEL_METADATA=$OPENWEBUI_DEFAULT_MODEL_METADATA" \
      "DEFAULT_MODEL_PARAMS=$OPENWEBUI_DEFAULT_MODEL_PARAMS" \
      "WEBUI_SECRET_KEY_FILE=$OPENWEBUI_DATA_DIR/.webui_secret_key" \
      "WEBUI_URL=$WEBUI_EXTERNAL_URL" \
      "HF_HOME=$HF_HOME_DIR" \
      "HUGGINGFACE_HUB_CACHE=$HF_HUB_CACHE_DIR" \
      "SENTENCE_TRANSFORMERS_HOME=$SENTENCE_TRANSFORMERS_HOME_DIR" \
      "TRANSFORMERS_CACHE=$TRANSFORMERS_CACHE_DIR" \
      /app/backend/start.sh
else
    IMAGE_PATH="/opt/images/jupyter-cpu.sif"
    exec apptainer exec --nv $HPC_APPTAINER_BIND "$IMAGE_PATH" {js} cmd {je} --ip=0.0.0.0
fi
"""
