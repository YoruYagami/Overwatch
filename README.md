# VulnLab

A Discord-based vulnerable machine lab platform inspired by VulnLab. Provides on-demand vulnerable machines for security training with WireGuard VPN access.

## Features

### Discord Bot Commands
- `/register` - Register account and optionally link Patreon
- `/activate <voucher>` - Activate subscription voucher (90/365 days)
- `/vpn` - Generate WireGuard VPN configuration
- `/machine <name>` - Control panel for single machines (start/stop/extend/reset)
- `/chain <name>` - Control panel for machine chains
- `/rtl <name>` - Red Team Labs (shared instances with voting)
- `/machines` - List all available machines
- `/chains` - List all available chains
- `/status` - Check subscription status

### Machine Features
- Dedicated instances per user
- Default 2-hour duration with extend option
- IP assigned at boot
- Auto-shutdown after timeout
- Reset to clean state

### Infrastructure
- **Hypervisor**: Proxmox VE (AWS migration planned)
- **VPN**: WireGuard with dynamic peer management
- **Database**: PostgreSQL
- **Subscription**: Voucher system + Patreon integration

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Proxmox VE server with VM templates
- WireGuard installed on VPN server
- Discord Bot token

### 1. Clone and Configure

```bash
git clone <repo-url>
cd vulnlab
cp .env.example .env
```

Edit `.env` with your configuration:
```env
# Discord
DISCORD_TOKEN=your_bot_token
DISCORD_GUILD_ID=your_guild_id

# Proxmox
PROXMOX_HOST=192.168.1.100
PROXMOX_USER=root@pam
PROXMOX_PASSWORD=your_password
PROXMOX_NODE=pve

# WireGuard
WG_SERVER_PUBLIC_KEY=your_server_pubkey
WG_SERVER_ENDPOINT=vpn.yourdomain.com:51820
WG_SERVER_PRIVATE_KEY=your_server_privkey
```

### 2. Start Services

```bash
docker-compose up -d
```

### 3. Create Machine Templates

Via API or directly in database:
```bash
curl -X POST http://localhost:8000/admin/machines/templates \
  -H "Content-Type: application/json" \
  -d '{
    "name": "dvwa",
    "display_name": "Damn Vulnerable Web App",
    "description": "Web application security training",
    "proxmox_template_id": 100,
    "difficulty": "easy",
    "category": "web",
    "os_type": "linux"
  }'
```

### 4. Generate Vouchers

```bash
curl -X POST http://localhost:8000/vouchers/generate \
  -H "Content-Type: application/json" \
  -d '{
    "voucher_type": "90_days",
    "count": 10
  }'
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│  Discord Bot    │────▶│  PostgreSQL     │
└────────┬────────┘     └─────────────────┘
         │                       ▲
         ▼                       │
┌─────────────────┐     ┌───────┴─────────┐
│  FastAPI        │────▶│  Proxmox VE     │
│  Backend        │     │  (VM Templates) │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│  WireGuard      │
│  VPN Server     │
└─────────────────┘
```

## Project Structure

```
vulnlab/
├── bot/                    # Discord Bot
│   ├── main.py            # Bot entry point
│   ├── cogs/              # Command modules
│   │   ├── registration.py
│   │   ├── vpn.py
│   │   ├── machines.py
│   │   ├── chains.py
│   │   └── rtl.py
│   └── utils/
│       ├── embeds.py      # Discord embeds
│       └── checks.py      # Permission checks
├── api/                    # FastAPI Backend
│   ├── main.py
│   ├── routers/
│   │   ├── machines.py
│   │   ├── vpn.py
│   │   ├── vouchers.py
│   │   └── admin.py
│   └── services/
│       ├── proxmox.py     # Proxmox integration
│       └── wireguard.py   # WireGuard management
├── db/                     # Database
│   ├── models.py          # SQLAlchemy models
│   └── database.py        # Connection setup
├── config/
│   └── settings.py        # Configuration
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Database Schema

### Core Tables
- `users` - Discord users with Patreon linking
- `vouchers` - Subscription voucher codes
- `subscriptions` - Active user subscriptions
- `vpn_configs` - WireGuard configurations

### Machine Tables
- `machine_templates` - Vulnerable machine definitions
- `machine_instances` - Running user instances
- `chains` - Multi-machine scenarios
- `chain_instances` - Running chain instances

### RT Labs
- `rtlabs` - Shared lab definitions
- `rtlab_sessions` - User participation and voting

## Proxmox Setup

### VM Template Requirements
1. Install QEMU Guest Agent
2. Configure cloud-init or static networking
3. Create a "clean" snapshot for reset functionality
4. Convert to template

### Network Configuration
- Create a dedicated VLAN for lab machines
- Configure firewall rules to isolate instances
- Set up NAT for internet access if needed

## WireGuard Setup

### Server Configuration
```bash
# Generate server keys
wg genkey | tee /etc/wireguard/privatekey | wg pubkey > /etc/wireguard/publickey

# Create wg0.conf
[Interface]
PrivateKey = <server_private_key>
Address = 10.10.0.1/16
ListenPort = 51820
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Enable and start
systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0
```

## API Endpoints

### Public
- `GET /health` - Health check

### Machines
- `GET /machines/templates` - List machine templates
- `GET /machines/instances` - List user instances
- `POST /machines/instances/{name}/start` - Start instance
- `POST /machines/instances/{id}/stop` - Stop instance

### VPN
- `GET /vpn/config/{user_id}` - Get VPN config
- `POST /vpn/generate` - Generate new config
- `POST /vpn/revoke/{user_id}` - Revoke config

### Vouchers
- `POST /vouchers/generate` - Generate vouchers (admin)
- `POST /vouchers/activate` - Activate voucher
- `GET /vouchers/{code}` - Get voucher info

### Admin
- `GET /admin/stats` - Platform statistics
- `GET /admin/users` - List users
- `POST /admin/users/{id}/ban` - Ban user
- `POST /admin/machines/templates` - Create template
- `POST /admin/chains` - Create chain
- `POST /admin/rtlabs` - Create RT Lab

## Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run bot
python -m bot.main

# Run API
uvicorn api.main:app --reload
```

## License

MIT License
