#!/usr/bin/env bash
# Run inside a new Ubuntu/PyTorch image as root.
# Native services support container instances without nested Docker.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"
runtime_dir="$project_dir/.runtime"
kafka_version="4.0.1"
kafka_name="kafka_2.13-$kafka_version"
kafka_dir="$runtime_dir/$kafka_name"
mkdir -p "$runtime_dir/logs" "$runtime_dir/redis" "$runtime_dir/kafka-data"

if [[ "${EUID}" -ne 0 ]]; then
    echo 'Please run this installer as root in the cloud image.' >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"
apt-get update
apt-get install -y openjdk-17-jre-headless redis-server curl ca-certificates \
    python3-venv python3-cairo libgl1 libglib2.0-0

if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv --system-site-packages .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt supervisor
# Plain Ubuntu images use CPU wheels. PyTorch base images reuse their GPU build.
if ! .venv/bin/python -c 'import torch, torchvision' >/dev/null 2>&1; then
    .venv/bin/python -m pip install torch==2.6.0 torchvision==0.21.0 \
        --index-url https://download.pytorch.org/whl/cpu
fi
.venv/bin/python -m pip install -r requirements-mineru.txt

if [[ ! -x "$kafka_dir/bin/kafka-server-start.sh" ]]; then
    archive="$runtime_dir/$kafka_name.tgz"
    source_url="https://archive.apache.org/dist/kafka/$kafka_version/$kafka_name.tgz"
    if [[ ! -f "$archive" || ! -f "$archive.sha512" ]]; then
        curl --fail --location --retry 3 "$source_url" -o "$archive"
        curl --fail --location --retry 3 "$source_url.sha512" -o "$archive.sha512"
    fi
    .venv/bin/python - "$archive" <<'PY'
import hashlib
from pathlib import Path
import re
import sys

archive = Path(sys.argv[1])
checksum_text = Path(str(archive) + '.sha512').read_text()
# Apache also publishes SHA-512 as wrapped groups after the filename.
match = re.search(r'\b[0-9a-fA-F]{128}\b', checksum_text)
expected = match[0] if match else re.sub(r'\s+', '', checksum_text.partition(':')[2])
if re.fullmatch(r'[0-9a-fA-F]{128}', expected) is None:
    raise SystemExit('Could not read the official Kafka SHA-512 checksum')
with archive.open('rb') as stream:
    digest = hashlib.sha512()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    actual = digest.hexdigest()
if actual.lower() != expected.lower():
    raise SystemExit('Kafka SHA-512 checksum mismatch')
PY
    tar -xzf "$archive" -C "$runtime_dir"
fi

cat > "$runtime_dir/kafka.properties" <<EOF
process.roles=broker,controller
node.id=1
controller.quorum.voters=1@127.0.0.1:9093
listeners=PLAINTEXT://127.0.0.1:9092,CONTROLLER://127.0.0.1:9093
advertised.listeners=PLAINTEXT://127.0.0.1:9092
controller.listener.names=CONTROLLER
listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
inter.broker.listener.name=PLAINTEXT
log.dirs=$runtime_dir/kafka-data
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
group.initial.rebalance.delay.ms=0
auto.create.topics.enable=true
num.partitions=1
EOF

if [[ ! -f "$runtime_dir/kafka-data/meta.properties" ]]; then
    cluster_id="$("$kafka_dir/bin/kafka-storage.sh" random-uuid)"
    "$kafka_dir/bin/kafka-storage.sh" format -t "$cluster_id" \
        -c "$runtime_dir/kafka.properties"
fi

# apt may start its default service. The new image uses Supervisor for restarts.
service redis-server stop || true

cat > "$runtime_dir/supervisord.conf" <<EOF
[unix_http_server]
file=$runtime_dir/supervisor.sock
chmod=0700

[supervisord]
logfile=$runtime_dir/logs/supervisord.log
pidfile=$runtime_dir/supervisord.pid
childlogdir=$runtime_dir/logs
environment=PATH="$project_dir/.venv/bin:%(ENV_PATH)s",MINERU_MODEL_SOURCE="modelscope",OMP_NUM_THREADS="4"

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://$runtime_dir/supervisor.sock

[program:redis]
command=/usr/bin/redis-server --bind 127.0.0.1 --port 6379 --appendonly yes --dir "$runtime_dir/redis"
autostart=true
autorestart=true
priority=10
redirect_stderr=true
stdout_logfile=$runtime_dir/logs/redis.log

[program:kafka]
command="$kafka_dir/bin/kafka-server-start.sh" "$runtime_dir/kafka.properties"
environment=KAFKA_HEAP_OPTS="-Xms512m -Xmx1024m"
autorestart=true
stopasgroup=true
killasgroup=true
priority=20
redirect_stderr=true
stdout_logfile=$runtime_dir/logs/kafka.log

[program:web]
command="$project_dir/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8014
directory=$project_dir
autorestart=true
priority=30
redirect_stderr=true
stdout_logfile=$runtime_dir/logs/web.log

[program:worker]
command="$project_dir/.venv/bin/python" -m app.worker
directory=$project_dir
autorestart=true
stopasgroup=true
killasgroup=true
stopwaitsecs=30
priority=40
redirect_stderr=true
stdout_logfile=$runtime_dir/logs/worker.log
EOF

if [[ ! -f .env ]]; then
    cp .env.example .env
fi

echo 'Installation finished. Configure the vision model in .env, then start services.'
echo "Run .venv/bin/python -m supervisor.supervisord -n -c '$runtime_dir/supervisord.conf'"
echo "Check .venv/bin/python -m supervisor.supervisorctl -c '$runtime_dir/supervisord.conf' status"
