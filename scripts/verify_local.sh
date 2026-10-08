#!/usr/bin/env bash
# Verifies that private services listen only on loopback and are unreachable via the LAN IP.
cd "$(dirname "$0")/.."
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || true)
fail=0
echo "Listeners:"
lsof -nP -iTCP -sTCP:LISTEN | awk '$9 ~ /:(8765|8080|11434)$/ {print "  " $1 " " $9}'
for port in 8765 8080 11434; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN | awk 'NR>1{print $9}' | grep -vqE '^(127\.0\.0\.1|\[::1\]|localhost):'; then
    echo "  ✗ port $port listens on a non-loopback address"; fail=1
  fi
  if [ -n "$LAN_IP" ] && curl -s -m 2 "http://$LAN_IP:$port/" >/dev/null; then
    echo "  ✗ $LAN_IP:$port is reachable"; fail=1
  else
    echo "  ✓ $port not reachable via LAN IP ${LAN_IP:-(none)}"
  fi
done
exit $fail
