#!/bin/bash
cd /root/workspace/hc-sidecar
# Ensure UPDATE_RESTART_COMMAND is empty (no Docker-Socket in container)
grep -q '^UPDATE_RESTART_COMMAND=' .env && \
  sed -i 's/^UPDATE_RESTART_COMMAND=.*/UPDATE_RESTART_COMMAND=/' .env || \
  echo 'UPDATE_RESTART_COMMAND=' >> .env
echo "UPDATE_RESTART_COMMAND set to empty"
