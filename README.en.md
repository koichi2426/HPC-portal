# HPC-portal

[日本語](./README.md) | **English**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

### 1. Project Overview
This project provides a platform on a single ARM server (`gx10-ac12`) to launch and use resource-limited applications (CPU, memory, and GPU) directly from a browser. By integrating JupyterHub with Slurm, it enables efficient resource management and secure access in a single-node environment.

- Launch compute applications from a browser via JupyterHub
- Control CPU, memory, and GPU resources through Slurm integration
- Access applications securely via per-job subdomains
- Run deployment and cleanup workflows with Ansible

---

### 2. System Architecture
The following sequence diagram shows the process interactions and port usage within a single OS.

```mermaid
sequenceDiagram
    autonumber
    participant User as 👤 User (Browser)
    participant CF as 🚀 cloudflared (*.<base-domain>)

    box "Host OS (gx10-ac12)"
        participant Proxy as 🌐 Proxy (CHP entry)<br/>(Port: 8000)
        participant JHub as 🧡 JupyterHub (Hub internal API)<br/>(Port: 8081)
        participant SlurmC as 👮 slurmctld<br/>(Port: 6817)
        participant SlurmD as 🖥️ slurmd<br/>(Port: 6818)
    end

    box "Isolated Container"
        participant App as 📦 App (JOBID: 4)<br/>(Port: dynamic, e.g. 20004)
    end

    Note over User, JHub: Phase 1: Application startup flow
    User->>CF: Access https://<hub-subdomain>.<base-domain>
    CF->>Proxy: Forward request to port 8000
    Proxy->>JHub: Show login and dashboard
    User->>JHub: Select app and click "Start"
    JHub->>SlurmC: Execute sbatch (issue JOBID 4)
    SlurmC->>SlurmD: Start job command (6817 -> 6818)
    SlurmD->>App: Start via apptainer exec (dynamic port)

    Note over JHub, App: Phase 2: Internal connectivity and URL mapping
    loop Wait until app is ready
        JHub->>App: Probe localhost:<dynamic-port>
    end
    Note right of JHub: Decide subdomain from JOBID "4"
    JHub->>Proxy: Sync route "job4.<base-domain>"<br/>to localhost:<dynamic-port>
    Note right of JHub: Routes are re-synced on startup/recovery

    Note over User, App: Phase 3: Access via dedicated subdomain
    User->>JHub: Click "job4" link in dashboard
    User->>CF: Access https://job4.<base-domain>
    CF->>Proxy: Wildcard forward (*.<base-domain> -> 8000)
    Proxy->>App: Match Host: job4.<base-domain><br/>and forward to dynamic port
    App-->>User: Render app screen transparently (new tab)
```

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
2. **Verify SSH connectivity** to the target host.
   ```bash
   ssh your_user@<target-ip>
   ```
3. **Configure inventory** by copying the sample file.
   ```bash
   cp inventory/production.ini.example inventory/production.ini
   # Edit production.ini (IP / ansible_user / domain variables)
   ```
4. **Set required secrets** in `group_vars/all/secret.yml`.
   ```bash
   cp group_vars/all/secret.yml.example group_vars/all/secret.yml
   # Edit secret.yml and set cloudflared_token
   ```

#### 🚀 Deploy
```bash
ansible-playbook -i inventory/production.ini site.yml
```

#### 🧹 Cleanup
```bash
ansible-playbook -i inventory/production.ini cleanup.yml
```

---

### 4. License

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
