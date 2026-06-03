#!/bin/bash
# Generate self-signed cert for tmos-shim
mkdir -p certs
openssl req -x509 -newkey rsa:4096 -keyout certs/key.pem -out certs/cert.pem \
  -days 3650 -nodes -subj "/CN=tmos-shim"
echo "Cert generated in certs/"
