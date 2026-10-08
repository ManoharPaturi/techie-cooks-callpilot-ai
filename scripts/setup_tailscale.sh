#!/usr/bin/env bash
# Configures the guest listener for Tailscale: finds this Mac's tailnet name + 100.x IP, fetches a real
# Let's Encrypt cert with `tailscale cert`, stores it OUTSIDE the repo (chmod 600) and writes .env.
set -euo pipefail
cd "$(dirname "$0")/.."

TS=$(command -v tailscale || true)
[ -z "$TS" ] && [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ] && TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
[ -z "$TS" ] && { echo "Tailscale not found. Install it from the Mac App Store and sign in."; exit 1; }

STATUS=$("$TS" status --json 2>/dev/null) || { echo "Tailscale is not running/signed in. Open the Tailscale app and log in."; exit 1; }
DNS=$(printf '%s' "$STATUS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
IP=$("$TS" ip -4 | head -1)
[ -z "$DNS" ] || [ -z "$IP" ] && { echo "Could not read tailnet name/IP"; exit 1; }
case "$DNS" in *.ts.net) ;; *) echo "MagicDNS name '$DNS' is not a *.ts.net name. Enable MagicDNS in the admin console."; exit 1;; esac
echo "This Mac on the tailnet: $DNS ($IP)"

TLS_DIR="$HOME/.callpilot/tls"; mkdir -p "$TLS_DIR"; chmod 700 "$TLS_DIR"
CERT="$TLS_DIR/$DNS.crt"; KEY="$TLS_DIR/$DNS.key"
echo "Requesting HTTPS certificate (needs 'HTTPS Certificates' enabled at https://login.tailscale.com/admin/dns)…"
if ! "$TS" cert --cert-file "$CERT" --key-file "$KEY" "$DNS"; then
  # The sandboxed App Store build may refuse paths outside its container; retry in its working dir and copy out.
  CONT="$HOME/Library/Containers/io.tailscale.ipn.macos/Data"
  (cd "$CONT" 2>/dev/null && "$TS" cert "$DNS") && cp "$CONT/$DNS.crt" "$CERT" && cp "$CONT/$DNS.key" "$KEY" \
    || { echo "tailscale cert failed — check HTTPS Certificates is enabled in the admin console."; exit 1; }
fi
chmod 600 "$KEY" "$CERT"

touch .env
python3 - "$IP" "$DNS" "$CERT" "$KEY" <<'PY'
import pathlib, sys
ip, dns, cert, key = sys.argv[1:]
vals = {"GUEST_ENABLED": "true", "GUEST_BIND": ip, "GUEST_PORT": "8443", "GUEST_PUBLIC_DOMAIN": dns,
        "GUEST_TLS_CERT": cert, "GUEST_TLS_KEY": key}
p = pathlib.Path(".env")
lines = [l for l in p.read_text().splitlines() if l.split("=", 1)[0].strip() not in vals]
p.write_text("\n".join(lines + [f"{k}={v}" for k, v in vals.items()]) + "\n")
PY
echo
echo "Done. .env updated (not committed). Restart with ./scripts/stop.sh && ./scripts/start.sh"
echo "Guest links will look like: https://$DNS:8443/join/<token>"
