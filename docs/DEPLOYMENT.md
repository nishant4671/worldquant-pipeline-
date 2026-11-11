# Production Deployment Guide: Azure Ubuntu Server

This guide documents the production deployment of the **WorldQuant BRAIN Autonomous Alpha Pipeline** on an Azure Ubuntu virtual machine (Ubuntu 22.04 / 24.04 LTS). 

The production deployment consists of two primary operational workloads:
1. **Systemd Service (`worldquant-listener.service`)**: A persistent background daemon running `main.py listen` to capture interactive Telegram button callbacks (Submit / Skip).
2. **Cron Job (`cron.log`)**: A scheduled nightly batch job running `main.py pipeline 50` at 02:00 UTC.

---

## 1. Environment & Prerequisites

### 1.1 Azure VM Specifications
- **Operating System**: Ubuntu Server 22.04 or 24.04 LTS
- **Architecture**: x86_64
- **Recommended Size**: Standard B2s (2 vCPU, 4 GiB RAM) or Standard D2s_v5
- **Deploy User**: `azureuser` (standard non-root user with `sudo` access)
- **Deployment Root**: `/home/azureuser/worldquant-pipeline`

### 1.2 System Packages & Dependencies
```bash
# Update package repositories
sudo apt-get update && sudo apt-get upgrade -y

# Install Python 3, venv, Git, and build essentials
sudo apt-get install -y python3 python3-venv python3-pip git curl sqlite3
```

---

## 2. Repository Installation & Secrets Setup

```bash
# Clone the repository
cd /home/azureuser
git clone https://github.com/your-username/worldquant-pipeline.git
cd worldquant-pipeline

# Create dedicated virtual environment
python3 -m venv venv
source venv/bin/activate

# Install production dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 2.1 Configure `.env` File
Create the `.env` configuration file at `/home/azureuser/worldquant-pipeline/.env`:

```ini
# WorldQuant BRAIN Platform Credentials
BRAIN_EMAIL=your_email@domain.com
BRAIN_PASSWORD=your_secure_password

# Telegram Bot Credentials
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789

# Google Gemini API Keys (Dynamic Key Rotation)
GEMINI_API_KEY_1=AIzaSyA1...
GEMINI_API_KEY_2=AIzaSyB2...
GEMINI_API_KEY_3=AIzaSyC3...

# Simulation Parameters & Quality Gates
MIN_SHARPE=1.25
MIN_FITNESS=1.0
MIN_TURNOVER=0.01
MAX_TURNOVER=0.70
TOP_N=4
DB_PATH=alphas.db
```

### 2.2 Restrict Secret Permissions
```bash
# Protect secrets from other local users
chmod 600 /home/azureuser/worldquant-pipeline/.env

# Verify environment imports cleanly
python config.py
```

---

## 3. Systemd Service: Telegram Listener Daemon

The Telegram listener polls for user callback button interactions (`Submit` / `Skip`). In production, this must run continuously as an isolated `systemd` service.

### 3.1 Create Systemd Unit File
Create the service unit file at `/etc/systemd/system/worldquant-listener.service`:

```ini
[Unit]
Description=WorldQuant BRAIN Telegram Interaction Listener Daemon
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=azureuser
Group=azureuser
WorkingDirectory=/home/azureuser/worldquant-pipeline
EnvironmentFile=/home/azureuser/worldquant-pipeline/.env

# The listener runs in 600s polling blocks; Restart=always seamlessly re-invokes it
ExecStart=/home/azureuser/worldquant-pipeline/venv/bin/python main.py listen

Restart=always
RestartSec=10

# Standard streams redirected to pipeline.log
StandardOutput=append:/home/azureuser/worldquant-pipeline/pipeline.log
StandardError=append:/home/azureuser/worldquant-pipeline/pipeline.log

# Resource and process limits
LimitNOFILE=65536
KillMode=process

[Install]
WantedBy=multi-user.target
```

### 3.2 Enable and Start the Service
```bash
# Reload systemd configuration
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable worldquant-listener.service

# Start service immediately
sudo systemctl start worldquant-listener.service

# Check service status
sudo systemctl status worldquant-listener.service
```

### 3.3 Managing the Listener Service
- **Check Status**: `sudo systemctl status worldquant-listener.service`
- **Restart Service**: `sudo systemctl restart worldquant-listener.service`
- **Stop Service**: `sudo systemctl stop worldquant-listener.service`
- **View Systemd Logs**: `journalctl -u worldquant-listener.service -f`

---

## 4. Cron Job: Overnight Alpha Discovery Pipeline

The discovery pipeline runs autonomously every night at **02:00 UTC** to formulate 50 candidate hypotheses, validate AST rules, critique via PM persona, simulate concurrently on BRAIN, and dispatch passing candidates to Telegram.

### 4.1 Schedule Crontab Entry
Open the user crontab editor:
```bash
crontab -e
```

Add the following schedule line:
```cron
# WorldQuant BRAIN Overnight Alpha Pipeline (02:00 UTC daily)
0 2 * * * cd /home/azureuser/worldquant-pipeline && /home/azureuser/worldquant-pipeline/venv/bin/python main.py pipeline 50 >> /home/azureuser/worldquant-pipeline/cron.log 2>&1
```

### 4.2 Verify Crontab Installation
```bash
crontab -l
```

---

## 5. Log Management & Monitoring

The system generates two primary operational log files in `/home/azureuser/worldquant-pipeline/`:

| Log File | Source | Description |
|---|---|---|
| `pipeline.log` | `main.py`, `AlphaEngine`, `api_client`, `llm_client`, `notifier` | Comprehensive structured application logs (simulation requests, rate limit backoffs, PM critique decisions, Telegram messages). |
| `cron.log` | Cron execution | Standard output and standard error from scheduled nightly cron runs. |

### 5.1 Real-Time Log Monitoring
To tail application execution logs:
```bash
# Tail main pipeline log
tail -f /home/azureuser/worldquant-pipeline/pipeline.log

# Tail cron execution log
tail -f /home/azureuser/worldquant-pipeline/cron.log

# Follow systemd listener daemon
journalctl -u worldquant-listener.service -f -n 50
```

### 5.2 Automated Log Rotation (`logrotate`)
To prevent logs from consuming disk space, configure Ubuntu's `logrotate`:

Create `/etc/logrotate.d/worldquant-pipeline`:
```text
/home/azureuser/worldquant-pipeline/pipeline.log
/home/azureuser/worldquant-pipeline/cron.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    copytruncate
    create 0640 azureuser azureuser
}
```

Test logrotate configuration:
```bash
sudo logrotate -d /etc/logrotate.d/worldquant-pipeline
```

---

## 6. Disaster Recovery & Health Checks

### 6.1 Database Backup
The SQLite database stores all historical simulation results, hashes, and submission statuses:
```bash
# Create timestamped backup of SQLite database
sqlite3 /home/azureuser/worldquant-pipeline/alphas.db ".backup /home/azureuser/backups/alphas_$(date +%Y%m%d).db"
```

### 6.2 Health Check Command
Run a quick diagnostic to verify API connectivity and token retrieval:
```bash
cd /home/azureuser/worldquant-pipeline
./venv/bin/python main.py auth
./venv/bin/python main.py status
```
