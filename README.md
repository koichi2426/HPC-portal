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

### 2. システムアーキテクチャ

単一ノード上で JupyterHub、Slurm、共有 Ollama、LiteLLM を連携させます。外部公開は Cloudflare Tunnel 経由だけで、Ollama と PostgreSQL はホスト内部からのみ利用します。

#### 全体構成

```mermaid
flowchart LR
    User[利用者]
    CF[Cloudflare Tunnel]

    subgraph Host[単一ノード]
        Proxy[configurable-http-proxy<br/>公開入口 :8000]
        JHub[JupyterHub<br/>ポータル]
        Slurm[Slurm]
        Apps[利用者ごとのアプリ<br/>JupyterLab / Open WebUI]
        LiteLLM[LiteLLM<br/>API Gateway]
        Ollama[共有 Ollama]
        DB[(PostgreSQL)]
        Models[(共有モデル保存領域)]
    end

    User --> CF
    CF -->|Hub・アプリURL| Proxy
    Proxy --> JHub
    Proxy --> Apps
    JHub -->|動的ルートを登録| Proxy
    JHub -->|ジョブ投入| Slurm
    Slurm --> Apps
    Apps -->|利用者別 Virtual Key| LiteLLM
    CF -->|LLM API・管理UI| LiteLLM
    LiteLLM <--> DB
    LiteLLM --> Ollama
    Ollama --> Models
```

#### 起動・推論の流れ

```mermaid
sequenceDiagram
    autonumber
    participant User as 利用者（ブラウザ）
    participant CF as Cloudflare Tunnel

    box "単一ノード"
        participant Proxy as configurable-http-proxy<br/>公開入口: 8000
        participant JHub as JupyterHub / ポータル<br/>Hub内部: 8081
        participant Slurm as Slurm<br/>slurmctld / slurmd
        participant App as 利用者ごとの Slurm ジョブ<br/>JupyterLab / Open WebUI
        participant LiteLLM as LiteLLM API Gateway<br/>127.0.0.1:4000
        participant Ollama as 共有 Ollama Slurm ジョブ<br/>127.0.0.1:11434
        participant DB as PostgreSQL<br/>127.0.0.1:5432
    end

    Note over User,App: フェーズ1：ポータルからアプリを起動
    User->>CF: Hub またはアプリ用URLへアクセス
    CF->>Proxy: Hub / job用ワイルドカードを転送
    Proxy->>JHub: ログイン・ダッシュボードを転送
    JHub->>User: ログイン・ダッシュボードを表示
    User->>JHub: JupyterLab または Open WebUI を起動
    JHub->>Slurm: sbatch で利用者のジョブを投入
    Slurm->>App: Apptainer コンテナを起動
    loop 起動待ち
        JHub->>App: 動的ポートへ疎通確認
    end
    JHub->>Proxy: job用URLと動的ポートの対応を登録
    Proxy->>App: 利用者のアプリ画面を転送

    Note over JHub,DB: フェーズ2：Open WebUI の利用者別認可
    JHub->>LiteLLM: 利用者の Virtual Key 状態を確認・必要時に発行
    LiteLLM->>DB: Key・利用量・設定を保存
    JHub->>App: 有効な利用者別 Key を渡して Open WebUI を起動

    Note over User,Ollama: フェーズ3：モデルへのリクエスト
    User->>App: Open WebUI でメッセージを送信
    App->>LiteLLM: OpenAI互換 API（利用者別 Virtual Key）
    LiteLLM->>DB: 利用量・Key状態を確認・記録
    LiteLLM->>Ollama: モデル推論を要求
    Ollama-->>LiteLLM: 推論結果
    LiteLLM-->>App: OpenAI互換レスポンス
    App-->>User: 応答を表示

    Note over User,LiteLLM: 外部API利用時
    User->>CF: LLM API / 管理UIへアクセス
    CF->>LiteLLM: API / 管理UIを転送
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

   - **ホームディレクトリ**（`/home/<ansible_user>/`）には、Slurm 経由で起動するアプリのデータが置かれます。共有 Ollama のモデルは `/srv/ollama/models` に置かれます
   - 手元の PC から、そのユーザーで **SSH 公開鍵認証**できること
   - `site.yml` は `become: true` で root 昇格するため、**パスワードなし sudo**（`sudo` グループ等）が必要です

   ```bash
   # サーバー側の例（Ubuntu）。your_user は production.ini の ansible_user と同じ名前にする
   sudo adduser your_user
   sudo usermod -aG sudo your_user
   # 手元: ssh-copy-id your_user@<target-ip>
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
| `make smoke` | 実機の主要サービス・API・配置物を読み取り専用で確認 |
| `make check` | ドライラン（`--check --diff`） |
| `make deploy` | ジョブを維持し、変更されたコンポーネントだけを安全に反映 |
| `make deploy-restart` | 全ジョブ停止・関連サービス再起動を伴う全体反映（`restart`確認あり） |
| `make cleanup` | サービス・設定のクリーンアップ（モデル・DBは残す） |
| `make cleanup-purge-data` | モデル・DBを含む完全削除（日本語確認あり） |
| `make common` | OS共通設定を差分反映。OSは自動再起動しない |
| `make jupyterhub` | JupyterHubだけを差分反映。Hub再起動時もジョブを維持 |
| `make slurm` | Slurmだけを差分反映。ジョブ実行中に設定差分があれば未変更のままスキップ |
| `make postgres` | PostgreSQLだけを差分反映 |
| `make litellm` | PostgreSQL・LiteLLMだけを差分反映 |
| `make ollama` | shared Ollamaの次回起動設定だけを差分反映 |
| `make apptainer` | ApptainerとSIFを差分反映。実行中コンテナは維持 |
| `make cloudflared` | cloudflaredだけを差分反映 |
| `make status` | Slurm ジョブ・ディスク空き |
| `make gpu` | GPU / VRAM |
| `make services` | サービス・shared Ollama 状態・主要ログ |
| `make processes` | 実行ユーザー / hpc-ollama の残存プロセス確認 |

別のインベントリを使う場合: `make deploy INV=inventory/staging.ini`

#### 🚀 デプロイ実行

```bash
make deploy
make smoke
```

`make smoke` はユーザー、ジョブ、モデル、パスワードなどを変更しません。必須サービス、Slurm、PostgreSQL、GPU、Pydantic、Apptainerイメージ、JupyterHub、ポータルのCSS・JavaScript、LiteLLMを確認します。共有Ollamaは、停止中なら正常なスキップ、起動中ならAPI応答まで確認します。

通常のデプロイはSlurmジョブを`scancel`しません。CSS・JavaScriptなどは配置だけで反映し、JupyterHub・LiteLLM・cloudflaredは設定変更時だけ対象サービスを再起動します。JupyterHubは再起動しても実行中アプリを維持します。アプリ起動時に渡す環境変数やリソース設定は、すでに起動中のアプリへは注入せず、次回起動から適用します。

Slurm設定に差分があり、実行中または待機中のジョブが存在する場合、通常デプロイはSlurm設定ファイルを変更せずにその部分だけをスキップし、ほかの更新は最後まで続けます。最後に`make deploy-restart`が必要だと表示されます。

全ジョブを停止してSlurm設定を含むすべての変更を反映し、関連サービスを再起動する場合は、次を実行して確認に`restart`と入力します。このジョブ維持方式を初めて反映するときも、こちらを使用してください。パッケージ取得・SIFビルド・Slurm候補設定の検証を先に完了し、成功した場合だけジョブを停止します。

```bash
make deploy-restart
```

停止したSlurmアプリケーションは自動起動しません。完了後、Ollamaなど必要なアプリケーションをポータルから起動してください。

Slurm設定は固定名の一時バックアップを使って一組として反映します。成功時、または失敗後の復元成功時にバックアップを削除するため、日時付きバックアップが増え続けることはありません。強制終了などでバックアップが残った場合は、上書きせず安全のため次回デプロイを中断します。

#### 🧹 クリーンアップ

```bash
make cleanup
```

`make cleanup` は `/srv/ollama/models` や LiteLLM DB などのデータを削除しません。データまで消す場合だけ、確認文に `削除する` と入力して完全削除を実行します。

```bash
make cleanup-purge-data
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

# JupyterHub / Slurm / LiteLLM / shared Ollama 状態（-b は root 権限が必要なとき）
ansible -i inventory/production.ini gx10 -b -m shell -a "systemctl is-active jupyterhub slurmctld slurmd cloudflared litellm postgresql || true; squeue; /usr/local/sbin/hpc-ollama status || true; journalctl -u jupyterhub -n 30 --no-pager"

# 残存プロセス（YOUR_USER は ansible_user に置き換え）
ansible -i inventory/production.ini gx10 -m shell -a "pgrep -au YOUR_USER -f 'open_webui|ollama|apptainer|jupyter' || true; pgrep -au hpc-ollama -f 'ollama|apptainer|curl' || true"

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
