# 🔍 Overwatch Security Scanner

**Overwatch** is a comprehensive Attack Surface Management (ASM) and vulnerability discovery platform built with modern web technologies. Inspired by ProjectDiscovery Cloud and similar platforms, Overwatch provides a powerful web interface for managing security scans, scheduling assessments, and analyzing results.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)

## ✨ Features

### 🎯 Comprehensive Reconnaissance
- **Subdomain Enumeration**: Aggregate subdomains using Subfinder, Assetfinder
- **DNS Resolution**: Validate live domains with DNSx
- **Port Scanning**: Discover open ports using Naabu (top 1000 ports)
- **HTTP Probing**: Detailed web service analysis with HTTPx
- **Technology Detection**: Identify web technologies, frameworks, and servers
- **Screenshot Capture**: Visual documentation of web applications

### 🚨 Vulnerability Assessment
- **Nuclei Integration**: Automated vulnerability scanning for CVEs, misconfigurations, and exposures
- **Risk Prioritization**: Intelligent scoring based on findings
- **Multi-severity Support**: Critical, High, Medium severity filtering

### 🌐 Modern Web Interface
- **Dark/Light Theme**: Modern, responsive UI with theme toggle
- **Real-time Progress**: Live scan progress with step-by-step updates
- **Scan Management**: Create, modify, delete, and rescan projects
- **Scheduling**: Queue scans or schedule for future execution
- **Report Viewing**: Beautiful HTML reports with interactive tables
- **Export Options**: Download results as JSON or CSV

### ⚡ Advanced Features
- **Job Queue System**: Manage multiple concurrent scans
- **Scan Scheduling**: Schedule scans for specific date/time
- **Proxy Support**: Full support for HTTP, HTTPS, SOCKS4, and SOCKS5 proxies with authentication
- **Progress Tracking**: Real-time progress bars with detailed status
- **Report Generation**: Self-contained HTML reports with embedded data
- **Multi-format Export**: JSON and CSV export for all datasets
- **Persistent Storage**: All scans and results are saved and accessible

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose (recommended)
- OR Python 3.11+ with Go 1.21+ for manual installation

### Option 1: Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/Overwatch.git
cd Overwatch

# Build and run with Docker Compose
docker-compose up -d

# Access the web interface
open http://localhost:8080
```

The web interface will be available at `http://localhost:8080`

### Option 2: Manual Installation

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Go-based security tools
go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install -v github.com/projectdiscovery/dnsx/cmd/dnsx@latest
go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest
go install -v github.com/projectdiscovery/naabu/v2/cmd/naabu@latest
go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
go install -v github.com/tomnomnom/assetfinder@latest

# Also install system dependencies
# On Ubuntu/Debian:
sudo apt-get install jq dnsutils whois curl chromium-browser

# Run the web application
python -m overwatch_web
```

## 📖 Usage

### Creating a New Scan

1. Click **"+ New Scan"** button
2. Enter a **Project Name** (e.g., "Example Corp Security Assessment")
3. Add **Target Domains** (one per line):
   ```
   example.com
   subdomain.example.com
   ```
4. Choose launch mode:
   - **▶️ Run Now**: Start scan immediately
   - **📋 Add to Queue**: Add to queue (runs when slot available)
   - **📅 Schedule**: Schedule for specific date/time

### Managing Scans

- **View Results**: Click "📊 View Report" to see detailed findings
- **Rescan**: Click 🔄 to rerun a scan with same targets
- **Cancel**: Click ⏹️ to stop a running scan
- **Modify**: Select scan and click "✏️ Modify" to update
- **Delete**: Select scan(s) and click "🗑️ Delete"

### Understanding Results

The HTML report includes:
- **Statistics Dashboard**: Total subdomains, live services, open ports, vulnerabilities
- **HTTP Services Table**: All discovered web services with status codes, titles, technologies
- **Port Scan Results**: Open ports per host
- **Vulnerabilities**: Detected security issues with severity ratings

### Scheduling Scans

1. Click **"+ New Scan"**
2. Fill in project details
3. Click **"📅 Schedule"**
4. Select date and time
5. Scan will automatically run at scheduled time

### Using Proxy Configuration

Overwatch supports HTTP, HTTPS, SOCKS4, and SOCKS5 proxies for all scanning operations:

1. When creating a scan, click **"🔒 Proxy Configuration"** to expand the proxy settings
2. Check **"Enable Proxy"**
3. Configure proxy settings:
   - **Proxy Type**: Select HTTP, HTTPS, SOCKS4, or SOCKS5
   - **Proxy Host**: Enter proxy server hostname or IP
   - **Proxy Port**: Enter proxy port number
   - **Username/Password** (optional): Add authentication if required

**Supported Proxy Types:**
- **HTTP/HTTPS**: Standard web proxies (e.g., Squid, Nginx)
- **SOCKS4**: SOCKS version 4 proxies
- **SOCKS5**: SOCKS version 5 proxies with authentication support

**Security Note:** Proxy credentials are **not stored** in the database for security reasons. You'll need to re-enter credentials for each scan that requires authentication.

**How It Works:**
- All reconnaissance tools (Subfinder, DNSx, HTTPx, Nuclei) will route traffic through the configured proxy
- Port scanning (Naabu) uses raw packets and may bypass proxies
- Proxy configuration is saved per-project (excluding credentials)
- Rescans automatically use the saved proxy settings

## 🏗️ Architecture

### Components

```
Overwatch/
├── overwatch_scanner.sh       # Main Bash scanner orchestrating all tools
├── overwatch_web/             # Flask web application
│   ├── server.py             # Backend API with job management
│   ├── templates/            # HTML templates
│   └── static/               # CSS and JavaScript
├── output/                    # Scan results and reports
│   └── projects/             # Per-project scan data
├── Dockerfile                # Container image definition
└── docker-compose.yml        # Container orchestration
```

### Scan Pipeline (10 Steps)

1. **Dependency Check**: Verify all required tools are installed
2. **Subdomain Enumeration**: Subfinder, Assetfinder
3. **DNS Resolution**: DNSx validation
4. **HTTP Probing**: HTTPx service discovery
5. **Port Scanning**: Naabu port detection
6. **Technology Analysis**: Stack identification
7. **Screenshot Capture**: Visual documentation
8. **Vulnerability Scanning**: Nuclei assessment
9. **Summary Generation**: Aggregate statistics
10. **Report Generation**: HTML report creation

### Technology Stack

- **Backend**: Python 3.11, Flask
- **Frontend**: Vanilla JavaScript, Modern CSS
- **Scanner**: Bash orchestrating ProjectDiscovery tools
- **Storage**: JSON-based file storage
- **Container**: Docker with Alpine/Debian base

## 🔧 Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OVERWATCH_MAX_CONCURRENT` | `1` | Maximum concurrent scans |

### Docker Configuration

Edit `docker-compose.yml` to customize:

```yaml
environment:
  - OVERWATCH_MAX_CONCURRENT=2  # Run 2 scans concurrently
volumes:
  - ./output:/app/output  # Persist scan results
ports:
  - "8080:8080"  # Change port if needed
```

## 📊 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/scans` | GET | List all scans |
| `/api/scans` | POST | Create new scan |
| `/api/scans/<slug>` | PUT | Update scan |
| `/api/scans/<slug>` | DELETE | Delete scan |
| `/api/scans/<slug>/rescan` | POST | Rescan project |
| `/api/scans/<slug>/cancel` | POST | Cancel running scan |
| `/api/status` | GET | Get job queue status |
| `/projects/<slug>/runs/<run_id>/report` | GET | View HTML report |
| `/projects/<slug>/runs/<run_id>/download/json` | GET | Download JSON data |
| `/projects/<slug>/runs/<run_id>/download/csv` | GET | Download CSV archive |

## 🛡️ Security Considerations

- **Authorization**: Add authentication before exposing to internet
- **Rate Limiting**: Implement rate limits for public deployments
- **Input Validation**: Domain validation is basic, enhance as needed
- **Network Access**: Scanner requires internet access for reconnaissance
- **Privileged Mode**: Docker runs privileged for raw sockets (Naabu)

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **ProjectDiscovery Team**: For the amazing open-source security tools (Nuclei, HTTPx, Naabu, DNSx, Subfinder)
- **TomNomNom**: For Assetfinder and innovative reconnaissance techniques

## 📧 Contact

For questions, issues, or suggestions, please open an issue on GitHub.

---

**⚠️ Disclaimer**: This tool is for authorized security testing only. Always obtain proper authorization before scanning targets you don't own.