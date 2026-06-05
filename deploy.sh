#!/usr/bin/env bash
# One-command Docker deploy for botxd2 on an Ubuntu server.
# Run from the botxd2 directory: bash deploy.sh
set -e

echo "==> Installing Docker..."
apt-get update -y
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc 2>/dev/null || true
apt-get install -y docker.io docker-compose-plugin || apt-get install -y docker.io docker-compose

echo "==> Building and starting botxd2..."
cd "$(dirname "$0")"
if docker compose version >/dev/null 2>&1; then
  docker compose up -d --build
  docker compose ps
else
  docker-compose up -d --build
  docker-compose ps
fi

echo "==> Done. Logs: docker compose logs -f"
