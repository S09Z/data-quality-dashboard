#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# gen_certs.sh
# Generate a self-signed TLS certificate for LOCAL DEVELOPMENT only.
# For production: replace with Let's Encrypt or your PKI-issued certs.
#
# Output: nginx/certs/server.crt  nginx/certs/server.key
# Usage:  bash scripts/gen_certs.sh
# -----------------------------------------------------------------------------
set -euo pipefail

CERT_DIR="$(cd "$(dirname "$0")/.." && pwd)/nginx/certs"
mkdir -p "$CERT_DIR"

echo "Generating self-signed certificate in $CERT_DIR …"

openssl req -x509 -newkey rsa:4096 -days 365 -nodes \
  -keyout "$CERT_DIR/server.key" \
  -out    "$CERT_DIR/server.crt" \
  -subj   "/C=TH/ST=Bangkok/L=Bangkok/O=DQD-Dev/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/server.crt"

echo "Done."
echo "  cert: $CERT_DIR/server.crt"
echo "  key:  $CERT_DIR/server.key"
echo ""
echo "NOTE: This is a self-signed cert for dev use only."
echo "      Your browser will show a security warning — that is expected."
echo "      For production, replace with a CA-signed certificate."
