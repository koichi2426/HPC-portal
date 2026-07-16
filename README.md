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

単一ノード上で JupyterHub、Slurm、共有 Ollama、LiteLLM、PostgreSQL、SearXNG を連携させます。JupyterLabとOpen WebUIは利用者ごとのSlurmジョブ、共有Ollamaは管理者が操作する共有SlurmジョブとしてApptainer上で動作します。外部公開はCloudflare Tunnel経由だけで、Ollama、PostgreSQL、SearXNGはホスト内部からのみ利用します。

#### 全体構成

```mermaid
flowchart LR
    User[利用者]
    CF[Cloudflare Tunnel]
    Search[外部検索サービス]

    subgraph Host[単一ノード]
        Proxy[configurable-http-proxy<br/>公開入口 :8000]
        JHub[JupyterHub<br/>ポータル]
        Slurm[Slurm]
        Apps[利用者ごとのアプリ<br/>JupyterLab / Open WebUI]
        LiteLLM[LiteLLM<br/>API Gateway]
        Ollama[共有 Ollama<br/>Slurmジョブ]
        SearXNG[SearXNG<br/>内部検索API]
        DB[(PostgreSQL)]
        Models[(共有モデル保存領域)]
    end

    User --> CF
    CF -->|Hub・アプリURL| Proxy
    Proxy --> JHub
    Proxy --> Apps
    JHub -->|動的ルートを登録| Proxy
    JHub -->|アプリ・共有Ollamaのジョブ投入| Slurm
    Slurm --> Apps
    Slurm --> Ollama
    JHub -->|ユーザー・Key・モデル管理| LiteLLM
    Apps -->|OpenAI互換API<br/>利用者別Virtual Key| LiteLLM
    Apps -->|Web検索| SearXNG
    SearXNG --> Search
    CF -->|LLM API・管理UI| LiteLLM
    LiteLLM <--> DB
    LiteLLM -->|ollama_chat| Ollama
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
        participant SearXNG as SearXNG<br/>127.0.0.1:8888
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

    opt Web検索を利用
        App->>SearXNG: 検索語を内部JSON APIへ送信
        SearXNG-->>App: 複数検索サービスの結果を返す
    end

    Note over User,LiteLLM: 外部API利用時
    User->>CF: LLM API / 管理UIへアクセス
    CF->>LiteLLM: API / 管理UIを転送
```

Open WebUIはLiteLLMのOpenAI互換`/v1/chat/completions`を利用し、LiteLLMはポータル管理モデルを`ollama_chat/<モデル名>`として共有Ollamaへ中継します。モデルのpull・削除後は、HPCポータルが管理対象のLiteLLMモデルを同期します。Web検索には内部SearXNGを使用します。

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
| `make test` | ローカルでpytestを実行（実機接続なし） |
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
| `make searxng` | SearXNGとOpen WebUIのWeb検索設定を差分反映 |
| `make cloudflared` | cloudflaredだけを差分反映 |
| `make status` | Slurm ジョブ・ディスク空き |
| `make gpu` | GPU / VRAM |
| `make services` | サービス・shared Ollama 状態・主要ログ |
| `make processes` | 実行ユーザー / hpc-ollama の残存プロセス確認 |

別のインベントリを使う場合: `make deploy INV=inventory/staging.ini`

#### 🚀 デプロイ実行

通常の更新:

```bash
make deploy
```

実行中のSlurmジョブを維持したまま、変更された構成だけを反映します。

Slurm設定を含む全体更新:

```bash
make deploy-restart
```

確認に`restart`と入力すると全ジョブを停止して反映します。停止したアプリは自動起動しないため、完了後に必要なアプリをポータルから起動してください。

<details>
<summary>デプロイの動作詳細</summary>

`make deploy`はジョブ実行中にSlurm設定差分を検出すると、その設定だけを保留して残りを反映し、最後に`make deploy-restart`が必要だと表示します。アプリ起動設定の変更は次回起動から反映されます。Slurm設定は固定名の一時バックアップを使い、成功時または復元成功時に削除します。

</details>

#### 🔎 Web検索（SearXNG）

SearXNGはApptainer上のsystemdサービスとして常時起動し、`127.0.0.1`だけで待ち受けます。Cloudflare Tunnelには公開しません。新規Open WebUI DB・新規モデルではWeb検索を初期ONにし、利用者はチャットごとにOFFへ切り替えられます。

初回デプロイ前に、ランダムな秘密値を生成します。

```bash
openssl rand -hex 32
```

出力をGit管理外の`group_vars/all/secret.yml`へ設定します。

```yaml
searxng_secret_key: "生成した値"
```

SearXNG関連だけを反映する場合:

```bash
make searxng
make smoke
```

実行中のOpen WebUIには起動時の環境変数が残るため、検索設定はOpen WebUIを停止して再起動した後に反映されます。また、Open WebUIの設定はDBへ保存されるため、既存DBで管理画面から変更済みの値は環境変数より優先される場合があります。既存DBは自動変更しません。

#### 🧪 開発環境・テスト

[uv](https://docs.astral.sh/uv/) をインストール後、リポジトリ直下で実行します。

##### 初回セットアップ・依存更新時

```bash
uv sync --dev
```

Python 3.12の`.venv`を作成し、開発用依存を同期します。

##### pytest（deploy前）

```bash
make test
```

実機へ接続せず、Pythonの入力検証・権限・処理分岐を確認します。

##### スモークテスト（deploy後）

```bash
make smoke
```

実機へ読み取り専用で接続して主要機能を確認します。ユーザー、ジョブ、モデル、パスワードは変更しません。SearXNGのJSON検索APIも確認し、共有Ollamaは停止中ならスキップします。

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
