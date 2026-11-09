#!/usr/bin/env bash
# 启动楼道消防隐患识别服务。
#   ./run.sh              默认 HTTPS 8443（手机可用）
#   PORT=9000 ./run.sh    换端口
#   NO_HTTPS=1 ./run.sh   纯 HTTP（仅本机 localhost 可用摄像头）
#   RELOAD=1 ./run.sh     开发模式，改代码自动重载
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
fi

PORT="${PORT:-8443}"
CERT_DIR="certs"
CERT="$CERT_DIR/cert.pem"
KEY="$CERT_DIR/key.pem"

# 端口被占用时给出可执行的提示，而不是直接抛 bind 错误
if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 $PORT 已被占用，通常是上一次启动的服务还没有退出。"
  echo "查看进程： lsof -nP -iTCP:$PORT -sTCP:LISTEN"
  echo "结束进程： kill <PID>"
  echo "或换端口： PORT=8444 ./run.sh"
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "提示：未找到 .env，将使用默认配置。"
  echo "      请先执行：cp .env.example .env，然后填写 DASHSCOPE_API_KEY。"
  echo
fi

lan_ip() {
  local ip=""
  if command -v ipconfig >/dev/null 2>&1; then
    ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
    [ -n "$ip" ] || ip="$(ipconfig getifaddr en1 2>/dev/null || true)"
  fi
  if [ -z "$ip" ] && command -v hostname >/dev/null 2>&1; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  printf '%s' "$ip"
}

make_cert() {
  mkdir -p "$CERT_DIR"
  local ip
  ip="$(lan_ip)"

  if command -v mkcert >/dev/null 2>&1; then
    echo "使用 mkcert 生成证书（首次建议先执行一次：mkcert -install）"
    # shellcheck disable=SC2086
    mkcert -cert-file "$CERT" -key-file "$KEY" localhost 127.0.0.1 ${ip:+"$ip"}
    return
  fi

  echo "使用 openssl 生成自签证书（手机首次访问需手动信任）"
  local alt="DNS.1 = localhost
IP.1 = 127.0.0.1"
  if [ -n "$ip" ]; then
    alt="$alt
IP.2 = $ip"
  fi

  cat > "$CERT_DIR/openssl.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no

[dn]
CN = stairwell-risk.local

[v3_req]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt

[alt]
$alt
EOF

  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "$KEY" -out "$CERT" \
    -config "$CERT_DIR/openssl.cnf" >/dev/null 2>&1
}

print_urls() {
  local ip
  ip="$(lan_ip)"
  echo
  echo "────────────────────────────────────────────"
  echo " 本机访问 : https://localhost:$PORT"
  if [ -n "$ip" ]; then
    echo " 手机访问 : https://$ip:$PORT"
    echo "            （手机需与电脑连同一个 Wi-Fi）"
  else
    echo " 未检测到局域网 IP，请手动查看「系统设置 → 网络」"
  fi
  echo "────────────────────────────────────────────"
  echo
}

if [ "${NO_HTTPS:-0}" = "1" ]; then
  echo "HTTP 模式：只有 localhost 可以调用摄像头，手机访问会被浏览器拦截。"
  echo "本机打开：http://localhost:$PORT"
  exec "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" ${RELOAD:+--reload}
fi

if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  make_cert
fi

print_urls

exec "$PYTHON" -m uvicorn main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --ssl-certfile "$CERT" \
  --ssl-keyfile "$KEY" \
  ${RELOAD:+--reload}
