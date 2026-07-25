# HPC-portal — よく使う Ansible 操作を make で実行
# 使い方: make help

SHELL := /bin/bash
.DEFAULT_GOAL := help

INV          ?= inventory/production.ini
PLAYBOOK     ?= ansible-playbook
ANSIBLE      ?= ansible
PB           := $(PLAYBOOK) -i $(INV)
ANSIBLE_ARGS := -i $(INV)

.PHONY: help setup test check ping smoke deploy deploy-restart cleanup cleanup-purge-data \
	common nfs-mounts slurm postgres litellm ollama jupyterhub apptainer searxng cloudflared \
	search-mcp status gpu cuda services processes

help: ## ターゲット一覧
	@printf '\nHPC-portal Makefile\n\n'
	@grep -E '^[a-zA-Z0-9_.-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@printf '\n例: make deploy   make jupyterhub   make status\n\n'

setup: ## インベントリ・secret・NFS設定を初期化（設定済みの値は維持）
	@test -f $(INV) || cp inventory/production.ini.example $(INV)
	@test -f group_vars/all/secret.yml || cp group_vars/all/secret.yml.example group_vars/all/secret.yml
	@test -f group_vars/all/nfs_mounts.yml || cp group_vars/all/nfs_mounts.yml.example group_vars/all/nfs_mounts.yml
	@python3 scripts/setup_secrets.py group_vars/all/secret.yml
	@echo "OK: $(INV)、group_vars/all/secret.yml、group_vars/all/nfs_mounts.yml を確認してください"

test: ## ローカルでpytestを実行（実機接続なし）
	uv run pytest

check-inv:
	@test -f $(INV) || { echo "エラー: $(INV) がありません。make setup を実行してください"; exit 1; }

check: check-inv ## 変更内容のドライラン（--check --diff）
	$(PB) site.yml --check --diff

ping: check-inv ## 接続確認
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -m ping

smoke: check-inv ## 実機の主要機能を読み取り専用で確認
	$(PB) smoke.yml

deploy: check-inv ## ジョブを維持して差分デプロイ
	$(PB) site.yml

deploy-restart: check-inv ## ジョブ停止・サービス再起動を伴う全体デプロイ
	@printf '実行中ジョブを停止し、関連サービスを再起動します。続行するには「restart」と入力してください: '; \
	read -r confirm; \
	if [ "$$confirm" != "restart" ]; then \
		echo "中止しました"; \
		exit 1; \
	fi
	$(PB) site_restart.yml

cleanup: check-inv ## 環境クリーンアップ (cleanup.yml)
	$(PB) cleanup.yml

cleanup-purge-data: check-inv ## モデル・DBを含む完全削除（要確認）
	@printf 'モデル・DBを含むデータを完全削除します。続行するには「削除する」と入力してください: '; \
	read -r confirm; \
	if [ "$$confirm" != "削除する" ]; then \
		echo "中止しました"; \
		exit 1; \
	fi
	$(PB) cleanup.yml
	$(PB) cleanup_purge_data.yml

common: check-inv ## common ロールのみ
	$(PB) site.yml --tags common

nfs-mounts: check-inv ## NASの読み取り専用NFS設定のみ
	$(PB) site.yml --tags nfs_mounts

slurm: check-inv ## slurm ロールのみ
	$(PB) site.yml --tags slurm

postgres: check-inv ## postgres ロールのみ
	$(PB) site.yml --tags postgres

litellm: check-inv ## LiteLLM / PostgreSQL ロールのみ
	$(PB) site.yml --tags litellm

ollama: check-inv ## shared Ollama ロールのみ
	$(PB) site.yml --tags ollama

jupyterhub: check-inv ## jupyterhub ロールのみ
	$(PB) site.yml --tags jupyterhub

apptainer: check-inv ## apptainer ロールのみ
	$(PB) site.yml --tags apptainer

searxng: check-inv ## SearXNGとOpen WebUI検索設定を差分反映
	$(PB) site.yml --tags apptainer,searxng,search_mcp,litellm,jupyterhub

search-mcp: check-inv ## LLM APIのWeb検索MCPを差分反映
	$(PB) site.yml --tags search_mcp,litellm

cloudflared: check-inv ## cloudflared ロールのみ
	$(PB) site.yml --tags cloudflared

status: check-inv ## Slurm ジョブ・ディスク空き
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -m shell -a "squeue; echo '---'; df -h /"

gpu: check-inv ## GPU 一覧・VRAM
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -m shell -a "nvidia-smi -L; nvidia-smi --query-gpu=memory.total,memory.used --format=csv"

cuda: check-inv ## CUDA Toolkit / nvcc 確認
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -b -m shell -a "set -e; echo '=== nvcc (PATH) ==='; (command -v nvcc && nvcc --version) || echo 'nvcc: not in PATH'; echo '=== cuda install roots ==='; ls -d /usr/local/cuda* 2>/dev/null || true; for d in /usr/local/cuda /usr/local/cuda-*; do [ -x \"$$d/bin/nvcc\" ] && echo \"found: $$d/bin/nvcc\" && $$d/bin/nvcc --version; done; echo '=== nvidia-smi ==='; nvidia-smi -L"

services: check-inv ## JupyterHub / Slurm / LiteLLM / SearXNG / shared Ollama 状態
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -b -m shell -a "echo '--- systemd'; systemctl is-active jupyterhub slurmctld slurmd cloudflared litellm searxng hpc-search-mcp postgresql || true; echo '--- squeue'; squeue || true; echo '--- hpc-ollama'; if [ -x /usr/local/sbin/hpc-ollama ]; then /usr/local/sbin/hpc-ollama status || true; else echo 'hpc-ollama: not installed'; fi; echo '--- jupyterhub log'; journalctl -u jupyterhub -n 30 --no-pager; echo '--- litellm log'; journalctl -u litellm -n 20 --no-pager; echo '--- searxng log'; journalctl -u searxng -n 20 --no-pager; echo '--- search mcp log'; journalctl -u hpc-search-mcp -n 20 --no-pager"

processes: check-inv ## 実行ユーザー / hpc-ollama の残存プロセス確認
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -m shell -a "echo '--- ansible user'; pgrep -au \$$(whoami) -f 'open_webui|ollama|apptainer|jupyter' || true; echo '--- hpc-ollama'; pgrep -au hpc-ollama -f 'ollama|apptainer|curl' || true"
