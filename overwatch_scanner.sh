#!/usr/bin/env bash
set -euo pipefail
set -o errtrace

# Color codes for logging
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Logging functions
info() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] [+]${NC} $*"; }
warning() { echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] [!]${NC} $*"; }
error() { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] [-]${NC} $*"; }
step() { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] [*]${NC} $*"; }

# Error trap
log_err() {
    local ec=$?
    local cmd=${BASH_COMMAND}
    error "Exit ${ec} at ${BASH_SOURCE[0]}:${BASH_LINENO[0]} while running: ${cmd}"
}
trap log_err ERR

# Script start time
SCRIPT_START_TIME=$(date +%s)

# Cleanup handler
script_cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        error "Scanner exited with code $exit_code"
        error "Check logs at: $RUN_DIR/logs/scan.log"
    else
        info "Scanner completed successfully"
        local end_time=$(date +%s)
        local duration=$((end_time - SCRIPT_START_TIME))
        local minutes=$((duration / 60))
        local seconds=$((duration % 60))
        info "Total execution time: ${minutes}m ${seconds}s"
    fi
}
trap script_cleanup EXIT

# Counters
SUBDOMAIN_COUNT=0
LIVE_DNS_COUNT=0
LIVE_HTTP_COUNT=0
OPEN_PORTS_COUNT=0
TOTAL_STEPS=10

# Proxy Configuration
PROXY_ENABLED="${PROXY_ENABLED:-false}"
PROXY_TYPE="${PROXY_TYPE:-http}"  # http, socks4, socks5
PROXY_HOST="${PROXY_HOST:-}"
PROXY_PORT="${PROXY_PORT:-}"
PROXY_USER="${PROXY_USER:-}"
PROXY_PASS="${PROXY_PASS:-}"

# Scan Options
SKIP_SUBDOMAIN_ENUM="${SKIP_SUBDOMAIN_ENUM:-false}"

# Setup proxy environment variables if enabled
setup_proxy() {
    if [ "$PROXY_ENABLED" = "true" ] && [ -n "$PROXY_HOST" ] && [ -n "$PROXY_PORT" ]; then
        local proxy_url=""

        # Build proxy URL with authentication if provided
        if [ -n "$PROXY_USER" ] && [ -n "$PROXY_PASS" ]; then
            proxy_url="${PROXY_TYPE}://${PROXY_USER}:${PROXY_PASS}@${PROXY_HOST}:${PROXY_PORT}"
        else
            proxy_url="${PROXY_TYPE}://${PROXY_HOST}:${PROXY_PORT}"
        fi

        # Set environment variables for different proxy types
        if [ "$PROXY_TYPE" = "http" ] || [ "$PROXY_TYPE" = "https" ]; then
            export HTTP_PROXY="$proxy_url"
            export HTTPS_PROXY="$proxy_url"
            export http_proxy="$proxy_url"
            export https_proxy="$proxy_url"
            info "HTTP/HTTPS Proxy enabled: ${PROXY_HOST}:${PROXY_PORT}"
        elif [ "$PROXY_TYPE" = "socks4" ] || [ "$PROXY_TYPE" = "socks5" ]; then
            export ALL_PROXY="$proxy_url"
            export HTTP_PROXY="$proxy_url"
            export HTTPS_PROXY="$proxy_url"
            info "SOCKS Proxy enabled: ${PROXY_TYPE}://${PROXY_HOST}:${PROXY_PORT}"
        fi

        # Save proxy info to run directory
        cat > "$RUN_DIR/proxy_config.json" <<EOF
{
    "enabled": true,
    "type": "$PROXY_TYPE",
    "host": "$PROXY_HOST",
    "port": "$PROXY_PORT",
    "authenticated": $([ -n "$PROXY_USER" ] && echo "true" || echo "false")
}
EOF
    else
        info "No proxy configured"
        echo '{"enabled": false}' > "$RUN_DIR/proxy_config.json"
    fi
}

# Check if required tools are installed
check_dependencies() {
    info "[1/${TOTAL_STEPS}] Checking dependencies..."
    local missing_tools=()
    local required_tools=("subfinder" "assetfinder" "dnsx" "httpx" "naabu" "nuclei" "jq" "curl" "dig" "whois")

    for tool in "${required_tools[@]}"; do
        if ! command -v "$tool" &>/dev/null; then
            missing_tools+=("$tool")
        fi
    done

    if [ ${#missing_tools[@]} -ne 0 ]; then
        error "Missing required tools:"
        for tool in "${missing_tools[@]}"; do
            echo -e "${RED}  - $tool${NC}"
        done
        warning "Install missing tools with: go install -v github.com/projectdiscovery/tool@latest"
        exit 1
    fi
    info "All required tools are available"
}

# Validate input
if [ "$#" -lt 1 ]; then
    error "Usage: $0 <targets_file>"
    exit 1
fi

TARGETS_FILE="$1"
if [[ ! -f "$TARGETS_FILE" || ! -r "$TARGETS_FILE" ]]; then
    error "File '$TARGETS_FILE' not found or not readable"
    exit 1
fi

# Setup output directory
RUN_DIR="output/run-$(date +%Y%m%d%H%M%S)"
mkdir -p "$RUN_DIR"/{logs,raw,screenshots,reports}

if [[ ! -w "$RUN_DIR" ]]; then
    error "Output directory '$RUN_DIR' is not writable"
    exit 1
fi

# Redirect logs
exec 2>"$RUN_DIR/logs/scan.log"
set -x

# Setup proxy if configured
setup_proxy

# File paths
ALL_SUBDOMAINS="$RUN_DIR/raw/all_subdomains.txt"
LIVE_SUBDOMAINS="$RUN_DIR/raw/live_subdomains.txt"
LIVE_HTTP="$RUN_DIR/raw/live_http.txt"
HTTPX_JSON="$RUN_DIR/httpx.json"
DNSX_JSON="$RUN_DIR/dnsx.json"
NAABU_JSON="$RUN_DIR/naabu.json"
NUCLEI_JSON="$RUN_DIR/nuclei.json"
PORTS_JSON="$RUN_DIR/ports.json"

> "$ALL_SUBDOMAINS"
> "$LIVE_SUBDOMAINS"
> "$LIVE_HTTP"

# Check dependencies
check_dependencies

# Step 2: Subdomain Enumeration (Optional)
if [ "$SKIP_SUBDOMAIN_ENUM" = "true" ]; then
    info "[2/${TOTAL_STEPS}] Subdomain enumeration skipped (using provided targets only)"
    # Use targets directly without subdomain discovery
    cat "$TARGETS_FILE" | tr '[:upper:]' '[:lower:]' | sort -u > "$ALL_SUBDOMAINS"
    SUBDOMAIN_COUNT=$(wc -l < "$ALL_SUBDOMAINS")
    info "Using $SUBDOMAIN_COUNT provided target(s)"
else
    info "[2/${TOTAL_STEPS}] Enumerating subdomains..."
    step "Running subfinder..."
    subfinder -dL "$TARGETS_FILE" -all -silent -o "$RUN_DIR/raw/subfinder.txt" 2>/dev/null || true

    step "Running assetfinder..."
    cat "$TARGETS_FILE" | assetfinder --subs-only 2>/dev/null > "$RUN_DIR/raw/assetfinder.txt" || true

    # Combine and deduplicate
    cat "$RUN_DIR/raw/subfinder.txt" "$RUN_DIR/raw/assetfinder.txt" 2>/dev/null | \
        tr '[:upper:]' '[:lower:]' | sort -u > "$ALL_SUBDOMAINS"
    SUBDOMAIN_COUNT=$(wc -l < "$ALL_SUBDOMAINS")
    info "Found $SUBDOMAIN_COUNT unique subdomains"
fi

if [ "$SUBDOMAIN_COUNT" -eq 0 ]; then
    warning "No targets to scan. Exiting."
    exit 0
fi

# Step 3: DNS Resolution
info "[3/${TOTAL_STEPS}] Resolving DNS records..."
dnsx -l "$ALL_SUBDOMAINS" -silent -json -o "$DNSX_JSON" 2>/dev/null || true
cat "$DNSX_JSON" | jq -r '.host' 2>/dev/null | sort -u > "$LIVE_SUBDOMAINS"
LIVE_DNS_COUNT=$(wc -l < "$LIVE_SUBDOMAINS")
info "Found $LIVE_DNS_COUNT live domains"

if [ "$LIVE_DNS_COUNT" -eq 0 ]; then
    warning "No live domains found. Exiting."
    exit 0
fi

# Step 4: HTTP Probing
info "[4/${TOTAL_STEPS}] Probing HTTP/HTTPS services..."
httpx -l "$LIVE_SUBDOMAINS" -silent -json -o "$HTTPX_JSON" \
    -status-code -title -tech-detect -content-length -web-server \
    -follow-redirects -random-agent -retries 2 -timeout 10 2>/dev/null || true

cat "$HTTPX_JSON" | jq -r '.url' 2>/dev/null | sort -u > "$LIVE_HTTP"
LIVE_HTTP_COUNT=$(wc -l < "$LIVE_HTTP")
info "Found $LIVE_HTTP_COUNT live HTTP services"

# Step 5: Port Scanning
info "[5/${TOTAL_STEPS}] Scanning ports..."
naabu -l "$LIVE_SUBDOMAINS" -silent -json -o "$NAABU_JSON" \
    -top-ports 1000 -rate 1000 -retries 2 2>/dev/null || true

# Process port scan results
if [ -f "$NAABU_JSON" ] && [ -s "$NAABU_JSON" ]; then
    jq -s 'group_by(.host) | map({
        host: .[0].host,
        ip: .[0].ip,
        ports: map(.port) | sort,
        port_count: length
    })' "$NAABU_JSON" > "$PORTS_JSON" 2>/dev/null || echo "[]" > "$PORTS_JSON"
    OPEN_PORTS_COUNT=$(jq '[.[].port_count] | add // 0' "$PORTS_JSON")
    info "Found $OPEN_PORTS_COUNT open ports across all hosts"
else
    echo "[]" > "$PORTS_JSON"
    warning "No open ports detected"
fi

# Step 6: Technology Detection Enhancement
info "[6/${TOTAL_STEPS}] Analyzing technologies..."
if [ -f "$HTTPX_JSON" ] && [ -s "$HTTPX_JSON" ]; then
    jq '[.[] | {
        url: .url,
        host: .host,
        status_code: .status_code,
        title: .title,
        technologies: .tech,
        web_server: .webserver,
        content_length: .content_length
    }] | unique_by(.url)' "$HTTPX_JSON" > "$RUN_DIR/tech_stack.json"
else
    echo "[]" > "$RUN_DIR/tech_stack.json"
fi

# Step 7: Screenshot capture
info "[7/${TOTAL_STEPS}] Capturing screenshots..."
if [ "$LIVE_HTTP_COUNT" -gt 0 ] && [ "$LIVE_HTTP_COUNT" -lt 100 ]; then
    httpx -l "$LIVE_HTTP" -silent -screenshot -screenshot-path "$RUN_DIR/screenshots" \
        -system-chrome -timeout 15 2>/dev/null || true
    SCREENSHOT_COUNT=$(find "$RUN_DIR/screenshots" -type f 2>/dev/null | wc -l)
    info "Captured $SCREENSHOT_COUNT screenshots"
else
    warning "Skipping screenshots (too many targets or no live hosts)"
fi

# Step 8: Vulnerability Scanning with Nuclei
info "[8/${TOTAL_STEPS}] Running vulnerability scans..."
if [ "$LIVE_HTTP_COUNT" -gt 0 ]; then
    nuclei -l "$LIVE_HTTP" -silent -json -o "$NUCLEI_JSON" \
        -severity critical,high,medium -tags cve,exposure,misconfig \
        -rate-limit 50 -bulk-size 25 -c 25 2>/dev/null || true

    if [ -f "$NUCLEI_JSON" ] && [ -s "$NUCLEI_JSON" ]; then
        VULN_COUNT=$(wc -l < "$NUCLEI_JSON")
        info "Found $VULN_COUNT potential vulnerabilities"
    else
        echo "[]" > "$NUCLEI_JSON"
        info "No vulnerabilities detected"
    fi
else
    echo "[]" > "$NUCLEI_JSON"
fi

# Step 9: Generate summary
info "[9/${TOTAL_STEPS}] Generating summary..."
cat > "$RUN_DIR/summary.json" <<EOF
{
    "scan_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "targets": $(cat "$TARGETS_FILE" | jq -R -s 'split("\n") | map(select(length > 0))'),
    "statistics": {
        "total_subdomains": $SUBDOMAIN_COUNT,
        "live_dns": $LIVE_DNS_COUNT,
        "live_http": $LIVE_HTTP_COUNT,
        "open_ports": $OPEN_PORTS_COUNT,
        "vulnerabilities": $([ -f "$NUCLEI_JSON" ] && wc -l < "$NUCLEI_JSON" || echo 0)
    },
    "run_directory": "$RUN_DIR"
}
EOF

# Step 10: Generate HTML report
info "[10/${TOTAL_STEPS}] Generating HTML report..."
generate_html_report() {
    local summary=$(cat "$RUN_DIR/summary.json")
    local stats=$(echo "$summary" | jq '.statistics')

    cat > "$RUN_DIR/report.html" <<'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Overwatch Security Scan Report</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #333;
            padding: 20px;
            min-height: 100vh;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        header {
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }
        header h1 {
            font-size: 2.5rem;
            margin-bottom: 10px;
            font-weight: 700;
        }
        header p {
            font-size: 1.1rem;
            opacity: 0.9;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 40px;
            background: #f8f9fa;
        }
        .stat-card {
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            text-align: center;
            transition: transform 0.2s;
        }
        .stat-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        .stat-value {
            font-size: 3rem;
            font-weight: 700;
            color: #667eea;
            margin-bottom: 10px;
        }
        .stat-label {
            font-size: 0.9rem;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .content {
            padding: 40px;
        }
        .section {
            margin-bottom: 40px;
        }
        .section h2 {
            font-size: 1.8rem;
            color: #1e3c72;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 3px solid #667eea;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        thead {
            background: #667eea;
            color: white;
        }
        th, td {
            padding: 15px;
            text-align: left;
        }
        tbody tr:nth-child(even) {
            background: #f8f9fa;
        }
        tbody tr:hover {
            background: #e9ecef;
        }
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.85rem;
            font-weight: 600;
        }
        .badge-success { background: #28a745; color: white; }
        .badge-warning { background: #ffc107; color: #333; }
        .badge-danger { background: #dc3545; color: white; }
        .badge-info { background: #17a2b8; color: white; }
        footer {
            background: #1e3c72;
            color: white;
            text-align: center;
            padding: 20px;
            font-size: 0.9rem;
        }
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #999;
        }
        .empty-state svg {
            width: 100px;
            height: 100px;
            opacity: 0.3;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🔍 Overwatch Security Scan Report</h1>
            <p>Comprehensive Attack Surface Analysis</p>
            <p style="font-size: 0.9rem; margin-top: 10px; opacity: 0.8;">
                Generated: <span id="scan-date"></span>
            </p>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value" id="stat-subdomains">0</div>
                <div class="stat-label">Subdomains Found</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="stat-live-dns">0</div>
                <div class="stat-label">Live DNS Records</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="stat-live-http">0</div>
                <div class="stat-label">Live HTTP Services</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="stat-ports">0</div>
                <div class="stat-label">Open Ports</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="stat-vulns">0</div>
                <div class="stat-label">Vulnerabilities</div>
            </div>
        </div>

        <div class="content">
            <div class="section">
                <h2>📊 HTTP Services</h2>
                <div id="http-table-container"></div>
            </div>

            <div class="section">
                <h2>🔌 Port Scan Results</h2>
                <div id="ports-table-container"></div>
            </div>

            <div class="section">
                <h2>🚨 Vulnerabilities Detected</h2>
                <div id="vulns-table-container"></div>
            </div>
        </div>

        <footer>
            <p>Generated by Overwatch Security Scanner | Powered by ProjectDiscovery Tools</p>
        </footer>
    </div>

    <script>
        // Load and display data
        async function loadData() {
            try {
                // Load summary
                const summary = SUMMARY_DATA;
                const stats = summary.statistics;

                // Update statistics
                document.getElementById('scan-date').textContent = new Date(summary.scan_date).toLocaleString();
                document.getElementById('stat-subdomains').textContent = stats.total_subdomains.toLocaleString();
                document.getElementById('stat-live-dns').textContent = stats.live_dns.toLocaleString();
                document.getElementById('stat-live-http').textContent = stats.live_http.toLocaleString();
                document.getElementById('stat-ports').textContent = stats.open_ports.toLocaleString();
                document.getElementById('stat-vulns').textContent = stats.vulnerabilities.toLocaleString();

                // Load HTTP data
                const httpData = HTTPX_DATA;
                renderHttpTable(httpData);

                // Load port data
                const portsData = PORTS_DATA;
                renderPortsTable(portsData);

                // Load vulnerabilities
                const vulnsData = NUCLEI_DATA;
                renderVulnsTable(vulnsData);

            } catch (error) {
                console.error('Error loading data:', error);
            }
        }

        function renderHttpTable(data) {
            const container = document.getElementById('http-table-container');
            if (!data || data.length === 0) {
                container.innerHTML = '<div class="empty-state">No HTTP services detected</div>';
                return;
            }

            let html = '<table><thead><tr>';
            html += '<th>URL</th><th>Status</th><th>Title</th><th>Web Server</th><th>Technologies</th>';
            html += '</tr></thead><tbody>';

            data.slice(0, 100).forEach(item => {
                const statusClass = item.status_code >= 200 && item.status_code < 300 ? 'success' :
                                  item.status_code >= 300 && item.status_code < 400 ? 'warning' :
                                  item.status_code >= 400 ? 'danger' : 'info';
                html += '<tr>';
                html += `<td><a href="${item.url}" target="_blank">${item.url}</a></td>`;
                html += `<td><span class="badge badge-${statusClass}">${item.status_code || 'N/A'}</span></td>`;
                html += `<td>${item.title || 'N/A'}</td>`;
                html += `<td>${item.web_server || 'N/A'}</td>`;
                html += `<td>${Array.isArray(item.technologies) ? item.technologies.join(', ') : 'N/A'}</td>`;
                html += '</tr>';
            });

            html += '</tbody></table>';
            container.innerHTML = html;
        }

        function renderPortsTable(data) {
            const container = document.getElementById('ports-table-container');
            if (!data || data.length === 0) {
                container.innerHTML = '<div class="empty-state">No open ports detected</div>';
                return;
            }

            let html = '<table><thead><tr>';
            html += '<th>Host</th><th>IP Address</th><th>Open Ports</th><th>Port Count</th>';
            html += '</tr></thead><tbody>';

            data.slice(0, 100).forEach(item => {
                html += '<tr>';
                html += `<td>${item.host}</td>`;
                html += `<td>${item.ip || 'N/A'}</td>`;
                html += `<td>${item.ports ? item.ports.join(', ') : 'N/A'}</td>`;
                html += `<td><span class="badge badge-info">${item.port_count || 0}</span></td>`;
                html += '</tr>';
            });

            html += '</tbody></table>';
            container.innerHTML = html;
        }

        function renderVulnsTable(data) {
            const container = document.getElementById('vulns-table-container');

            // Parse JSONL if data is string
            let vulns = data;
            if (typeof data === 'string') {
                vulns = data.trim().split('\n').filter(l => l).map(l => {
                    try { return JSON.parse(l); } catch(e) { return null; }
                }).filter(v => v);
            }

            if (!vulns || vulns.length === 0) {
                container.innerHTML = '<div class="empty-state">✅ No vulnerabilities detected</div>';
                return;
            }

            let html = '<table><thead><tr>';
            html += '<th>Template</th><th>Severity</th><th>Host</th><th>Matched At</th><th>Type</th>';
            html += '</tr></thead><tbody>';

            vulns.slice(0, 100).forEach(item => {
                const severityClass = item.info?.severity === 'critical' ? 'danger' :
                                     item.info?.severity === 'high' ? 'warning' :
                                     item.info?.severity === 'medium' ? 'info' : 'success';
                html += '<tr>';
                html += `<td>${item.info?.name || item.template || 'Unknown'}</td>`;
                html += `<td><span class="badge badge-${severityClass}">${(item.info?.severity || 'info').toUpperCase()}</span></td>`;
                html += `<td>${item.host || 'N/A'}</td>`;
                html += `<td>${item['matched-at'] || item.matched || 'N/A'}</td>`;
                html += `<td>${item.type || 'N/A'}</td>`;
                html += '</tr>';
            });

            html += '</tbody></table>';
            container.innerHTML = html;
        }

        // Load data on page load
        loadData();
    </script>
</body>
</html>
HTMLEOF

    # Inject data into HTML
    local summary_json=$(cat "$RUN_DIR/summary.json" | jq -c .)
    local httpx_json=$(cat "$RUN_DIR/tech_stack.json" 2>/dev/null || echo "[]")
    local ports_json=$(cat "$PORTS_JSON" 2>/dev/null || echo "[]")
    local nuclei_data=""

    if [ -f "$NUCLEI_JSON" ] && [ -s "$NUCLEI_JSON" ]; then
        nuclei_data=$(cat "$NUCLEI_JSON" | jq -s -c .)
    else
        nuclei_data="[]"
    fi

    # Insert data into HTML before </script>
    sed -i "s|// Load and display data|const SUMMARY_DATA = $summary_json;\nconst HTTPX_DATA = $httpx_json;\nconst PORTS_DATA = $ports_json;\nconst NUCLEI_DATA = $nuclei_data;\n\n// Load and display data|" "$RUN_DIR/report.html"
}

generate_html_report
info "HTML report generated: $RUN_DIR/report.html"

info "Scan complete! Results saved to: $RUN_DIR"
info "Open the report: file://$PWD/$RUN_DIR/report.html"
