FROM golang:alpine AS builder

# Install build dependencies
RUN apk add --no-cache git bash curl

# Install Go-based security tools
RUN go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest && \
    go install -v github.com/projectdiscovery/dnsx/cmd/dnsx@latest && \
    go install -v github.com/projectdiscovery/httpx/cmd/httpx@latest && \
    go install -v github.com/projectdiscovery/naabu/v2/cmd/naabu@latest && \
    go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest && \
    go install -v github.com/tomnomnom/assetfinder@latest

FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    bash \
    curl \
    jq \
    dnsutils \
    whois \
    chromium \
    chromium-driver \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy Go tools from builder
COPY --from=builder /go/bin/* /usr/local/bin/

# Set up working directory
WORKDIR /app

# Copy application files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create output directory
RUN mkdir -p output/projects

# Make scanner script executable
RUN chmod +x overwatch_scanner.sh

# Expose web interface port
EXPOSE 8080

# Set environment variables
ENV OVERWATCH_MAX_CONCURRENT=1
ENV PYTHONUNBUFFERED=1

# Run the web application
CMD ["python", "-m", "overwatch_web"]
