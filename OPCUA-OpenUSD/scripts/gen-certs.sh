#!/usr/bin/env bash
# Generate a self-signed root CA + a wildcard cert for *.stack.local (and stack.local).
# Output goes into traefik/certs/ — gitignored. Idempotent.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$ROOT_DIR/traefik/certs"
mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

DAYS_CA=3650
DAYS_LEAF=825   # macOS Safari rejects > 825 days for leaf certs
HOST="${STACK_HOSTNAME:-stack.local}"

if [[ -f stack.local.crt && -f stack.local.key && -f rootCA.crt ]]; then
  echo "[gen-certs] Existing CA + cert present, skipping. Delete $CERT_DIR/*.{crt,key} to regenerate."
  exit 0
fi

# 1) Root CA
echo "[gen-certs] Generating root CA..."
openssl genrsa -out rootCA.key 4096 2>/dev/null
openssl req -x509 -new -nodes -sha256 -days "$DAYS_CA" \
  -key rootCA.key -out rootCA.crt \
  -subj "/CN=Axel Robot Twin Local Root CA/O=Axel/C=DE"

# 2) Leaf cert for *.stack.local + stack.local
echo "[gen-certs] Generating leaf cert for $HOST..."
openssl genrsa -out stack.local.key 2048 2>/dev/null
cat > stack.local.csr.cnf <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
CN = $HOST
O = Axel Robot Twin
C = DE

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $HOST
DNS.2 = *.$HOST
DNS.3 = localhost
IP.1  = 127.0.0.1
EOF

openssl req -new -key stack.local.key -out stack.local.csr -config stack.local.csr.cnf
openssl x509 -req -in stack.local.csr -CA rootCA.crt -CAkey rootCA.key -CAcreateserial \
  -out stack.local.crt -days "$DAYS_LEAF" -sha256 \
  -extensions v3_req -extfile stack.local.csr.cnf

rm -f stack.local.csr stack.local.csr.cnf rootCA.srl

echo
echo "[gen-certs] Done. Certs in $CERT_DIR:"
ls -la "$CERT_DIR"
echo
echo "[gen-certs] Trust the root CA on your Mac to silence browser warnings:"
echo "  scp <user>@<HOST_IP>:$CERT_DIR/rootCA.crt /tmp/root-ca.crt"
echo "  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain /tmp/root-ca.crt"
echo
echo "[gen-certs] Add to /etc/hosts on Mac (HOST_IP is the GPU host's LAN IP):"
echo "  <HOST_IP>  $HOST"
