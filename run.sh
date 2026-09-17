#!/bin/bash
set -euo pipefail

SCHEDULER_KEY="$HOME/Desktop/ssh-key-2026-08-24.key"
SCHEDULER_HOST="ubuntu@92.4.71.233"

BUILDER_KEY="$HOME/Desktop/DistributeML_key.pem"
BUILDER_HOST="azureuser@172.197.248.83"

WORKER_HOST="aorko@100.115.56.125"

echo "Deployment started at $(date)"

echo ">>> [1/3] Scheduler + Headscale Management + Gateway"
ssh -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    -i "$SCHEDULER_KEY" \
    "$SCHEDULER_HOST" \
    'cd "$HOME/Distributed_ML_Training_scheduler" &&
     git fetch origin main &&
     git checkout main &&
     git merge --ff-only origin/main &&
     sudo -n env REQUIRE_INTERACTIVE=1 \
       bash "$HOME/Distributed_ML_Training_scheduler/restart.sh"'

echo ">>> [2/3] UI + Docker Image Builder"
ssh -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    -i "$BUILDER_KEY" \
    "$BUILDER_HOST" \
    'cd "$HOME/Distributed_ML_Training_scheduler" &&
     git pull &&
     cd "$HOME/Distributed_ML_Training_scheduler/UI" &&
     bash restart.sh &&
     cd "$HOME/Distributed_ML_Training_scheduler/Docker_Image_Builder" &&
     docker compose down &&
     docker compose build &&
     docker compose up -d'

echo ">>> [3/3] Worker"
ssh -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    "$WORKER_HOST" \
    'set -e
     cd "$HOME/workplace/Distributed_ML_Training_scheduler"
     git pull
     cd "$HOME/workplace/Distributed_ML_Training_scheduler/Worker"
     tmux kill-session -t worker 2>/dev/null || true
     tmux new-session -d -s worker
     tmux send-keys -t worker \
       "cd $HOME/workplace/Distributed_ML_Training_scheduler/Worker && source venv/bin/activate && python3 main.py" C-m
     sleep 1
     tmux ls
     tmux capture-pane -p -t worker | tail -20'

echo ">>> Deployment completed at $(date)"
