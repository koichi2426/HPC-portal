# HPC-portal

**日本語** | [English](./README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

### 1. プロジェクト概要
このプロジェクトは、ARMサーバー1台（`gx10-ac12`）上で、CPU・メモリ・GPUのリソース制限を適用したアプリケーションをブラウザから即時起動・利用できる基盤を構築するものです。JupyterHubとSlurmを統合し、単一ノード環境で効率的なリソース管理とセキュアなアクセスを実現します。

- ブラウザから JupyterHub 経由で計算アプリを起動
- Slurm 連携による CPU / メモリ / GPU のリソース制御
- ジョブごとのサブドメイン経由で安全にアプリへアクセス
- Ansible によるデプロイとクリーンアップの一括実行

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
2. **SSH接続の確認**: デプロイ前提として、対象IPへSSHでログインできることを確認します。
   ```bash
   ssh your_user@<target-ip>
   ```
3. **インベントリの設定**: サンプルをコピーして `inventory/production.ini` を作成します。
   ```bash
   cp inventory/production.ini.example inventory/production.ini
   # production.ini を編集して IP / ansible_user / ドメイン変数を設定
   ```
4. **変数の設定**: `group_vars/all/secret.yml` に Cloudflare Tunnel トークン等を記述します。
   ```bash
   cp group_vars/all/secret.yml.example group_vars/all/secret.yml
   # secret.yml を編集して cloudflared_token を入力
   ```

#### 🚀 デプロイ実行
```bash
ansible-playbook -i inventory/production.ini site.yml
```

#### 🧹 クリーンアップ
```bash
ansible-playbook -i inventory/production.ini cleanup.yml
```

---

### 4. ライセンス

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)