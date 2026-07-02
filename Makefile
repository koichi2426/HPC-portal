# HPC-portal — よく使う Ansible 操作を make で実行
# 使い方: make help

SHELL := /bin/bash
.DEFAULT_GOAL := help

INV          ?= inventory/production.ini
PLAYBOOK     ?= ansible-playbook
ANSIBLE      ?= ansible
PB           := $(PLAYBOOK) -i $(INV)
ANSIBLE_ARGS := -i $(INV)

.PHONY: help setup check ping deploy deploy-safe cleanup \
	common slurm postgres litellm ollama jupyterhub apptainer cloudflared \
	status gpu cuda services processes

help: ## ターゲット一覧
	@printf '\nHPC-portal Makefile\n\n'
	@grep -E '^[a-zA-Z0-9_.-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@printf '\n例: make deploy   make jupyterhub   make status\n\n'

setup: ## インベントリ・secret の雛形をコピー（未作成時）
	@test -f $(INV) || cp inventory/production.ini.example $(INV)
	@test -f group_vars/all/secret.yml || cp group_vars/all/secret.yml.example group_vars/all/secret.yml
	@echo "OK: $(INV) と group_vars/all/secret.yml を確認してください"

check-inv:
	@test -f $(INV) || { echo "エラー: $(INV) がありません。make setup を実行してください"; exit 1; }

check: check-inv ## 変更内容のドライラン（--check --diff）
	$(PB) site.yml --check --diff

ping: check-inv ## 接続確認
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -m ping

deploy: check-inv ## フルデプロイ (site.yml)
	$(PB) site.yml

deploy-safe: check-inv ## 再起動抑止デプロイ (site_safe.yml)
	$(PB) site_safe.yml

cleanup: check-inv ## 環境クリーンアップ (cleanup.yml)
	$(PB) cleanup.yml

common: check-inv ## common ロールのみ
	$(PB) site.yml --tags common

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

cloudflared: check-inv ## cloudflared ロールのみ
	$(PB) site.yml --tags cloudflared

status: check-inv ## Slurm ジョブ・ディスク空き
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -m shell -a "squeue; echo '---'; df -h /"

gpu: check-inv ## GPU 一覧・VRAM
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -m shell -a "nvidia-smi -L; nvidia-smi --query-gpu=memory.total,memory.used --format=csv"

cuda: check-inv ## CUDA Toolkit / nvcc 確認
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -b -m shell -a "set -e; echo '=== nvcc (PATH) ==='; (command -v nvcc && nvcc --version) || echo 'nvcc: not in PATH'; echo '=== cuda install roots ==='; ls -d /usr/local/cuda* 2>/dev/null || true; for d in /usr/local/cuda /usr/local/cuda-*; do [ -x \"$$d/bin/nvcc\" ] && echo \"found: $$d/bin/nvcc\" && $$d/bin/nvcc --version; done; echo '=== nvidia-smi ==='; nvidia-smi -L"

services: check-inv ## JupyterHub / Slurm サービス状態
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -b -m shell -a "systemctl is-active jupyterhub slurmctld slurmd cloudflared litellm postgresql; journalctl -u jupyterhub -n 30 --no-pager; journalctl -u litellm -n 20 --no-pager"

processes: check-inv ## ユーザーの残存プロセス確認
	$(ANSIBLE) $(ANSIBLE_ARGS) gx10 -m shell -a "pgrep -au \$$(whoami) -f 'open_webui|ollama|apptainer|jupyter' || true"
