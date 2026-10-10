#!/bin/sh
# Self-signed HTTPS certificate for an in-house server without an internal CA. Users' PCs trust
# this one certificate (deploy/README.md §2-1). It is a leaf (CA:FALSE): a stolen server key
# cannot sign certificates for other sites, unlike a home-made CA that every PC trusts.
# Usage: sudo deploy/tls-selfsigned.sh <server DNS name> [server IP]
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run with sudo: the key must belong to root (nginx reads it without extra rights)" >&2; exit 2; }
[ $# -ge 1 ] && [ $# -le 2 ] || { echo "usage: $0 <server DNS name> [server IP]" >&2; exit 2; }
case $1 in *[!A-Za-z0-9.-]*|"") echo "bad DNS name: $1" >&2; exit 2;; esac
case ${2-} in *[!0-9.]*) echo "bad IPv4 address: $2" >&2; exit 2;; esac
mkdir -p config/tls
[ ! -e config/tls/server.crt ] || {
    echo "config/tls/server.crt exists. To renew, move it away first (PCs must then trust the new one)" >&2; exit 1; }
umask 077
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -sha256 -days 825 -nodes \
    -keyout config/tls/server.key -out config/tls/server.crt -subj "/CN=$1" \
    -addext "subjectAltName=DNS:$1${2:+,IP:$2}" -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature" -addext "extendedKeyUsage=serverAuth"
chmod 755 config config/tls && chmod 644 config/tls/server.crt  # server.key stays root 600
openssl x509 -in config/tls/server.crt -noout -subject -enddate -ext subjectAltName -fingerprint -sha256
openssl x509 -in config/tls/server.crt -noout -fingerprint -sha1  # what Windows shows as "Thumbprint"
echo "Give users config/tls/server.crt and a fingerprint above to check before trusting it."
