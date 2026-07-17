# HPC-portal

[日本語](./README.md) | **English**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

### 1. Project Overview
This project provides a platform on a single ARM server (`gx10-ac12`) to launch and use resource-limited applications (CPU, memory, and GPU) directly from a browser. By integrating JupyterHub with Slurm, it enables efficient resource management and secure access in a single-node environment.

- Launch compute applications from a browser via JupyterHub
- Control CPU, memory, and GPU resources through Slurm integration
- Access applications securely via per-job subdomains
- Run deployment and cleanup workflows with Ansible (`make` shortcuts for common tasks)

---

### 2. System Architecture

JupyterHub, Slurm, shared Ollama, LiteLLM, PostgreSQL, and SearXNG run together on one node. JupyterLab and Open WebUI run as per-user Slurm jobs, while shared Ollama runs in Apptainer as an administrator-managed shared Slurm job. External access goes through Cloudflare Tunnel only; Ollama, PostgreSQL, and SearXNG remain internal to the host.

#### Overview

```mermaid
flowchart TB
    User[User] --> CF[Cloudflare Tunnel]
    Proxy[configurable-http-proxy<br/>:8000]
    JHub[JupyterHub<br/>Portal]
    Slurm[Slurm]
    Apps[Per-user applications<br/>JupyterLab / Open WebUI]
    LiteLLM[LiteLLM<br/>API Gateway]
    SearXNG[SearXNG<br/>internal search API]
    Ollama[Shared Ollama]
    DB[(PostgreSQL)]
    Models[(Shared model storage)]
    Search[External search services]

    CF -->|Hub and application URLs| Proxy
    CF -->|LLM API and admin UI| LiteLLM
    Proxy --> JHub
    Proxy --> Apps
    JHub -->|Submit jobs| Slurm
    Slurm --> Apps
    Slurm --> Ollama
    JHub -->|Manage users, keys, and models| LiteLLM
    Apps -->|OpenAI-compatible API<br/>per-user Virtual Key| LiteLLM
    Apps -->|Web search| SearXNG
    LiteLLM <--> DB
    LiteLLM -->|ollama_chat| Ollama
    Ollama --> Models
    SearXNG --> Search

    classDef external fill:#f3f4f6,stroke:#6b7280,color:#111827
    classDef control fill:#dbeafe,stroke:#2563eb,color:#111827
    classDef workload fill:#dcfce7,stroke:#16a34a,color:#111827
    classDef service fill:#ede9fe,stroke:#7c3aed,color:#111827
    classDef data fill:#fef3c7,stroke:#d97706,color:#111827
    class User,CF,Search external
    class Proxy,JHub,Slurm control
    class Apps,Ollama workload
    class LiteLLM,SearXNG service
    class DB,Models data
```

Colors: gray=external, blue=portal and job management, green=Slurm workloads, purple=AI and search services, yellow=persistent data. Blue, green, purple, and yellow components run on the single node.

#### Startup and inference flow

```mermaid
sequenceDiagram
    autonumber
    participant User as User (browser)
    participant CF as Cloudflare Tunnel

    box "Single node"
        participant Proxy as configurable-http-proxy<br/>public entry: 8000
        participant JHub as JupyterHub / Portal<br/>Hub internal: 8081
        participant Slurm as Slurm<br/>slurmctld / slurmd
        participant App as Per-user Slurm job<br/>JupyterLab / Open WebUI
        participant LiteLLM as LiteLLM API Gateway<br/>127.0.0.1:4000
        participant Ollama as Shared Ollama Slurm job<br/>127.0.0.1:11434
        participant SearXNG as SearXNG<br/>127.0.0.1:8888
        participant DB as PostgreSQL<br/>127.0.0.1:5432
    end

    Note over User,App: Phase 1: Start an application from the portal
    User->>CF: Access a Hub or app URL
    CF->>Proxy: Forward Hub and job wildcard routes
    Proxy->>JHub: Forward login and dashboard traffic
    JHub->>User: Display login and dashboard
    User->>JHub: Start JupyterLab or Open WebUI
    JHub->>Slurm: Submit a per-user job with sbatch
    Slurm->>App: Start an Apptainer container
    loop Wait until ready
        JHub->>App: Probe the dynamic port
    end
    JHub->>Proxy: Register the job URL and dynamic port mapping
    Proxy->>App: Forward the user's application traffic

    Note over JHub,DB: Phase 2: Per-user Open WebUI authorization
    JHub->>LiteLLM: Check or issue the user's Virtual Key
    LiteLLM->>DB: Store key, usage, and configuration
    JHub->>App: Start Open WebUI with the active per-user key

    Note over User,Ollama: Phase 3: Send a model request
    User->>App: Send a message in Open WebUI
    App->>LiteLLM: OpenAI-compatible API with per-user Virtual Key
    LiteLLM->>DB: Check and record key status and usage
    LiteLLM->>Ollama: Request model inference
    Ollama-->>LiteLLM: Inference result
    LiteLLM-->>App: OpenAI-compatible response
    App-->>User: Display the response

    opt Use web search
        App->>SearXNG: Send the query to the internal JSON API
        SearXNG-->>App: Return aggregated search results
    end

    Note over User,LiteLLM: External API use
    User->>CF: Access the LLM API or admin UI
    CF->>LiteLLM: Forward API or admin UI traffic
```

Open WebUI uses LiteLLM's OpenAI-compatible `/v1/chat/completions` endpoint, and LiteLLM forwards portal-managed models to shared Ollama as `ollama_chat/<model>`. After a model is pulled or deleted, the HPC portal synchronizes the LiteLLM models it manages. Web search uses the internal SearXNG service.

---

### 3. Quick Start

#### 🛠 Prerequisites

1. **Install Ansible** in your execution environment.
   ```bash
   # macOS
   brew install ansible
   # Ubuntu
   sudo apt update && sudo apt install ansible -y
   ```
2. **Create the runtime user on the target host**: The playbooks do **not** create a Unix account. Create a user on the server whose login name matches **`ansible_user`** in `inventory/production.ini`.

   - Slurm-launched app data lives under that home directory. Shared Ollama models live in `/srv/ollama/models`
   - **SSH public-key login** from your control machine
   - **Passwordless sudo** is required (`site.yml` uses `become: true`)

   ```bash
   # On the server (Ubuntu). Use the same name as ansible_user in production.ini
   sudo adduser your_user
   sudo usermod -aG sudo your_user
   # On your laptop: ssh-copy-id your_user@<target-ip>
   ```

3. **Verify SSH connectivity** with that user:
   ```bash
   ssh <ansible_user>@<target-ip>
   ```
4. **Set up the inventory and secrets**:
   ```bash
   make setup
   # Set external values such as cloudflared_token in group_vars/all/secret.yml
   ```

   `make setup` generates only missing LiteLLM, PostgreSQL, and SearXNG secrets. It never replaces values that are already configured.

#### 📋 Common make targets

See [Makefile](./Makefile). Run `make help` for the full list.

##### Core operations

| Command | Description |
|---------|-------------|
| `make deploy` | Safely apply changed components while preserving Slurm jobs |
| `make deploy-restart` | Stop all jobs and restart related services, with `restart` confirmation |
| `make ping` | Connectivity check |
| `make check` | Dry run (`--check --diff`) |
| `make test` | Run pytest locally without connecting to the target host |
| `make smoke` | Run read-only checks against services, APIs, and deployed assets |

Override inventory: `make deploy INV=inventory/staging.ini`

##### Component updates

| Command | Description |
|---------|-------------|
| `make jupyterhub` | Apply JupyterHub changes while preserving running jobs |
| `make ollama` | Apply shared Ollama settings for the next start |
| `make apptainer` | Apply Apptainer and image changes while preserving running containers |
| `make litellm` | Apply PostgreSQL and LiteLLM changes only |
| `make searxng` | Apply SearXNG and Open WebUI web-search settings |
| `make common` | Apply common OS settings without rebooting the host |
| `make slurm` | Apply Slurm changes; leave its config untouched when active jobs require a restart |
| `make postgres` | Apply PostgreSQL changes only |
| `make cloudflared` | Apply cloudflared changes only |

##### Diagnostics

| Command | Description |
|---------|-------------|
| `make status` | Show Slurm jobs and disk space |
| `make services` | Show service, shared Ollama, and major log status |
| `make gpu` | Show GPU and VRAM status |
| `make processes` | Check remaining user and hpc-ollama processes |

##### Cleanup

| Command | Description |
|---------|-------------|
| `make cleanup` | Cleanup services and config while keeping model/DB data |
| `make cleanup-purge-data` | Delete model/DB data too, with confirmation |

#### 🚀 Deploy

Normal update:

```bash
make deploy
```

This applies changed components while preserving running Slurm jobs.

Full update including Slurm configuration:

```bash
make deploy-restart
```

Enter `restart` at the prompt to stop all jobs and apply the update. Stopped applications are not restarted automatically; start the required applications from the portal afterward.

<details>
<summary>Deployment behavior details</summary>

If `make deploy` detects pending Slurm configuration changes while jobs are active, it defers only that configuration, applies the remaining changes, and reports that `make deploy-restart` is required. App launch configuration changes take effect on the next start. Slurm configuration uses fixed-name temporary backups that are removed after success or successful rollback.

</details>

#### 🧪 Development environment and tests

After installing [uv](https://docs.astral.sh/uv/), run these commands in the repository root.

##### Initial setup and dependency updates

```bash
uv sync --dev
```

This creates a Python 3.12 `.venv` and synchronizes development dependencies.

##### pytest (before deployment)

```bash
make test
```

This runs locally without connecting to the target host and checks Python input validation, authorization, and control flow.

##### Smoke test (after deployment)

```bash
make smoke
```

This connects to the target host in read-only mode and checks major functionality, including the SearXNG JSON search API. It does not change users, jobs, models, or passwords. A stopped shared Ollama instance is skipped.

#### 🧹 Cleanup

```bash
make cleanup
```

`make cleanup` removes services and configuration but keeps data such as `/srv/ollama/models` and the LiteLLM database. Use the explicit purge target only when you want to delete model and DB data as well.

```bash
make cleanup-purge-data
```

#### 🔍 Remote diagnostics

Start with `make status`, `make gpu`, `make services`, or `make processes`.

<details>
<summary>Run ansible commands directly</summary>

```bash
ansible -i inventory/production.ini gx10 -m ping
ansible-playbook -i inventory/production.ini site.yml --tags jupyterhub
ansible-playbook -i inventory/production.ini site.yml --check --diff
```

</details>

---

### 4. License

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
