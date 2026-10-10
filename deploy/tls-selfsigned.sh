#!/bin/sh
# Self-signed HTTPS certificate for an in-house server without an internal CA. Users' PCs trust
# this one certificate (deploy/README.md §2-1). It is a leaf (CA:FALSE): a stolen server key
# cannot sign certificates for other sites, unlike a home-made CA that every PC trusts.
# Usage: sudo deploy/tls-selfsigned.sh <server DNS name> [server IP]
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run with sudo: the key must belong to root (nginx reads it without extra rights)" >&2; exit 2; }
[ $# -ge 1 ] && [ $# -le 2 ] || { echo "usage: $0 <server DNS name> [server IP]" >&2; exit 2; }
case $1 in *[!A-Za-z0-9.-]*|""|-*) echo "bad DNS name: $1" >&2; exit 2;; esac
case ${2-} in *[!0-9.]*) echo "bad IPv4 address: $2" >&2; exit 2;; esac
[ ! -L config ] && [ ! -L config/tls ] || { echo "config/tls must not be a symlink" >&2; exit 1; }
mkdir -p config/tls
[ "$(stat -c %u config/tls)" = 0 ] || { echo "config/tls must belong to root" >&2; exit 1; }
for f in server.crt server.key server.cer; do
    [ ! -e "config/tls/$f" ] && [ ! -L "config/tls/$f" ] || {
        echo "config/tls/$f exists. To renew, move config/tls away first (README §4)" >&2; exit 1; }
done
umask 077
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -sha256 -days 825 -nodes \
    -keyout config/tls/server.key -out config/tls/server.crt -subj "/CN=$1" \
    -addext "subjectAltName=DNS:$1${2:+,IP:$2}" -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature" -addext "extendedKeyUsage=serverAuth"
openssl x509 -in config/tls/server.crt -outform der -out config/tls/server.cer  # copy for PCs
chmod 755 config config/tls
chmod 644 config/tls/server.crt config/tls/server.cer  # server.key stays root 600
openssl x509 -in config/tls/server.crt -noout -subject -enddate -ext subjectAltName
echo "SHA256 (same as Windows: certutil -hashfile server.cer SHA256):"
sha256sum config/tls/server.cer | cut -d' ' -f1
echo "Give users config/tls/server.cer; announce the SHA256 above through another channel."
