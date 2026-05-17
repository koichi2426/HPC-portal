# HPC-portal

**日本語** | [English](./README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

### 1. プロジェクト概要
このプロジェクトは、ARMサーバー1台（`gx10-ac12`）上で、CPU・メモリ・GPUのリソース制限を適用したアプリケーションをブラウザから即時起動・利用できる基盤を構築するものです。JupyterHubとSlurmを統合し、単一ノード環境で効率的なリソース管理とセキュアなアクセスを実現します。

- ブラウザから JupyterHub 経由で計算アプリを起動
- Slurm 連携による CPU / メモリ / GPU のリソース制御
- ジョブごとのサブドメイン経由で安全にアプリへアクセス
- Ansible によるデプロイとクリーンアップの一括実行（`make` で主要コマンドを短縮）

---

### 2. システムアーキテクチャ（詳細設計）
単一OS内における各プロセスの連携と、使用ポートの詳細は以下の通りです。

```mermaid
sequenceDiagram
    autonumber
    participant User as 👤 User (Browser)
    participant CF as 🚀 cloudflared (*.<base-domain>)

    box "Host OS (gx10-ac12)"
        participant Proxy as 🌐 Proxy (CHP入口)<br/>(Port: 8000)
        participant JHub as 🧡 JupyterHub (Hub内部API)<br/>(Port: 8081)
        participant SlurmC as 👮 slurmctld<br/>(Port: 6817)
        participant SlurmD as 🖥️ slurmd<br/>(Port: 6818)
    end

    box "Isolated Container"
        participant App as 📦 App (JOBID: 4)<br/>(Port: 動的割当 例: 20004)
    end

    Note over User, JHub: 【フェーズ1：アプリケーションの起動プロセス】
    User->>CF: https://<hub-subdomain>.<base-domain> へアクセス
    CF->>Proxy: リクエスト転送 (8000番へ)
    Proxy->>JHub: ログイン・ダッシュボード表示
    User->>JHub: アプリ選択 & 「Start」ボタン押下
    JHub->>SlurmC: sbatch 実行指示 (JOBID 4発行)
    SlurmC->>SlurmD: ジョブ開始命令 (Port: 6817 -> 6818)
    SlurmD->>App: apptainer exec 起動 (動的ポートで待機)

    Note over JHub, App: 【フェーズ2：内部疎通とURLマッピング】
    loop 起動待ち
        JHub->>App: localhost:動的ポート へ疎通確認
    end
    Note right of JHub: JOBID「4」に基づきサブドメイン決定
    JHub->>Proxy: 「job4.<base-domain>」の転送先を<br/>localhost:動的ポート に同期登録
    Note right of JHub: routeは起動時/補修時に再同期される

    Note over User, App: 【フェーズ3：専用サブドメインによる個別アクセス】
    User->>JHub: ダッシュボード上の「job4 リンク」をクリック
    User->>CF: https://job4.<base-domain> へアクセス
    CF->>Proxy: ワイルドカード転送 (*.<base-domain> -> 8000)
    Proxy->>App: Host: job4.<base-domain> を識別して<br/>内部の動的ポートへ転送
    App-->>User: 透過的にアプリ画面を表示 (別タブ)
```

---

### 3. クイックスタート

#### 🛠 事前準備

1. **Ansibleのインストール**: 実行環境（手元のPC等）にAnsibleをインストールします。
   ```bash
   # macOS
   brew install ansible
   # Ubuntu
   sudo apt update && sudo apt install ansible -y
   ```
2. **デプロイ先に運用ユーザーを作成**: Playbook は Unix ユーザーを自動作成しません。後述する `inventory/production.ini` の **`ansible_user` と同じ名前のユーザー**を、対象サーバー（gx10 等）にあらかじめ用意してください。

   - **ホームディレクトリ**（`/home/<ansible_user>/`）に、LLM モデル（`~/models`）、Ollama（`~/.ollama`）、Slurm 経由で起動するアプリのデータが置かれます
   - 手元の PC から、そのユーザーで **SSH 公開鍵認証**できること
   - `site.yml` は `become: true` で root 昇格するため、**パスワードなし sudo**（`sudo` グループ等）が必要です

   ```bash
   # サーバー側の例（Ubuntu）
   sudo adduser kamlab
   sudo usermod -aG sudo kamlab
   # 手元: ssh-copy-id kamlab@<target-ip>
   ```

3. **SSH接続の確認**: 上記ユーザーでログインできることを確認します。
   ```bash
   ssh <ansible_user>@<target-ip>
   ```
4. **インベントリと秘密情報の設定**（雛形コピー）:
   ```bash
   make setup
   # inventory/production.ini … IP / ansible_user / ドメイン変数
   # group_vars/all/secret.yml … cloudflared_token など
   ```

#### 📋 よく使う make コマンド

プロジェクト直下の [Makefile](./Makefile) に、Ansible の定番操作をまとめています。一覧は `make help` で確認できます。

| コマンド | 内容 |
|----------|------|
| `make ping` | 接続確認 |
| `make check` | ドライラン（`--check --diff`） |
| `make deploy` | フルデプロイ（`site.yml`） |
| `make deploy-safe` | 再起動抑止デプロイ（`site_safe.yml`） |
| `make cleanup` | クリーンアップ（`cleanup.yml`） |
| `make jupyterhub` | JupyterHub ロールのみ |
| `make slurm` | Slurm ロールのみ |
| `make models` | LLM / Ollama モデル取得のみ |
| `make apptainer` | Apptainer ロールのみ |
| `make status` | Slurm ジョブ・ディスク空き |
| `make gpu` | GPU / VRAM |
| `make services` | サービス状態・JupyterHub ログ |
| `make processes` | 残存プロセス確認 |

別のインベントリを使う場合: `make deploy INV=inventory/staging.ini`

#### 🚀 デプロイ実行

```bash
make deploy
```

#### 🧹 クリーンアップ

```bash
make cleanup
```

#### 🔍 リモートでの原因調査

挙動がおかしいときは、まず `make status` / `make gpu` / `make services` / `make processes` を試してください。

<details>
<summary>ansible コマンドを直接使う場合</summary>

ホスト名 `gx10` は `inventory/production.ini` のグループ名に合わせてください。

```bash
# 接続確認
ansible -i inventory/production.ini gx10 -m ping

# Slurm ジョブ・ノード割当
ansible -i inventory/production.ini gx10 -m shell -a "squeue; scontrol show node \$(hostname -s) -o"

# GPU / VRAM
ansible -i inventory/production.ini gx10 -m shell -a "nvidia-smi -L; nvidia-smi --query-gpu=memory.total,memory.used --format=csv"

# JupyterHub / Slurm サービス（-b は root 権限が必要なとき）
ansible -i inventory/production.ini gx10 -b -m shell -a "systemctl is-active jupyterhub slurmctld slurmd; journalctl -u jupyterhub -n 30 --no-pager"

# 残存プロセス（YOUR_USER は ansible_user に置き換え）
ansible -i inventory/production.ini gx10 -m shell -a "pgrep -au YOUR_USER -f 'open_webui|ollama|apptainer|jupyter' || true"

# 部分デプロイ
ansible-playbook -i inventory/production.ini site.yml --tags jupyterhub
ansible-playbook -i inventory/production.ini site.yml --tags slurm

# ドライラン
ansible-playbook -i inventory/production.ini site.yml --check --diff
```

</details>

---

### 4. ライセンス

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)