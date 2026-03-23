#!/bin/bash
# 仅重启 Temu 容器（使用当前已构建的镜像），不影响其他应用
set -e
cd "$(dirname "$0")"
CONTAINER_NAME="temu-marketing-tools"
IMAGE_NAME="temu-marketing-tools"
PORT="8082:5000"

echo "停止旧容器..."
docker stop $CONTAINER_NAME 2>/dev/null || true
docker rm $CONTAINER_NAME 2>/dev/null || true
echo "启动新容器 (端口 $PORT)..."
docker run -d -p $PORT --name $CONTAINER_NAME $IMAGE_NAME
echo "完成。访问: http://<服务器IP>:8082"
docker ps --filter name=$CONTAINER_NAME
