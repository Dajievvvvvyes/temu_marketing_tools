#!/bin/bash
# Temu 营销工具 - 部署到 8082，不影响 80/8080/8081 上的现有应用
set -e
cd "$(dirname "$0")"
echo "Building image..."
docker build -t temu-marketing-tools .
echo "Stopping old container (if any)..."
docker stop temu-marketing-tools 2>/dev/null || true
docker rm temu-marketing-tools 2>/dev/null || true
echo "Starting container on port 8082..."
docker run -d -p 8082:5000 --name temu-marketing-tools temu-marketing-tools
echo "Done. Access: http://<服务器IP>:8082"
docker ps --filter name=temu-marketing-tools
