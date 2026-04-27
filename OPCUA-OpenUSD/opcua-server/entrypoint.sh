#!/usr/bin/env bash
# Generate a self-signed application instance cert if missing, then start the server.
# Idempotent: re-runs are safe.
set -euo pipefail

CERT_DIR="/app/certs"
CERT="$CERT_DIR/server_cert.der"
KEY="$CERT_DIR/server_key.pem"

mkdir -p "$CERT_DIR/trusted" "$CERT_DIR/issued"

if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  echo "[opcua-entrypoint] No app cert found, generating..."
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
    -keyout "$KEY" -outform DER -out "$CERT" \
    -subj "/CN=Axel Robot Twin OPCUA Server/O=Axel/C=DE" \
    -addext "subjectAltName=URI:urn:axel:robot:server,DNS:opcua-server,DNS:localhost,IP:127.0.0.1" \
    -addext "keyUsage=digitalSignature,nonRepudiation,keyEncipherment,dataEncipherment,keyCertSign" \
    -addext "extendedKeyUsage=serverAuth,clientAuth"
  echo "[opcua-entrypoint] App cert at $CERT"
fi

exec python -m src.server
