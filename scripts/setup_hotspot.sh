#!/usr/bin/env bash
# Direct iPhone Personal Hotspot mode (no router, no domain, no Tailscale).
#
# Creates (once) a local CA that is NAME-CONSTRAINED to the iPhone hotspot subnet 172.20.10.0/28 only, so even if
# trusted on the phone it cannot vouch for any real website. Then issues a guest server cert covering every client
# address on that subnet (172.20.10.2-14) and points .env at the Mac's current hotspot IP.
#
#   ./scripts/setup_hotspot.sh          # configure for the current hotspot IP
#   ./scripts/setup_hotspot.sh --ca     # also reveal the CA file to AirDrop to the iPhone (optional, removes warning)
set -euo pipefail
cd "$(dirname "$0")/.."

IP=""
for ifc in en0 en1 en2; do
  cand=$(ipconfig getifaddr "$ifc" 2>/dev/null || true)
  case "$cand" in 172.20.10.*) IP=$cand; break;; esac
done
if [ -z "$IP" ]; then
  echo "This Mac is not on an iPhone Personal Hotspot (no 172.20.10.x address)."
  echo "iPhone: Settings → Personal Hotspot → Allow Others to Join. Mac: join that Wi-Fi network, then re-run."
  exit 1
fi
echo "Mac hotspot address: $IP"

DIR="$HOME/.callpilot/hotspot-tls"
mkdir -p "$DIR"; chmod 700 "$DIR"
CA_KEY="$DIR/ca.key"; CA_CRT="$DIR/CallPilot-Hotspot-CA.crt"
KEY="$DIR/guest.key"; CRT="$DIR/guest.crt"

if [ ! -f "$CA_KEY" ]; then
  echo "Creating name-constrained local CA (valid for 172.20.10.0/28 only)…"
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 -keyout "$CA_KEY" -out "$CA_CRT" \
    -subj "/CN=CallPilot Hotspot Demo CA (172.20.10.0-28 only)/O=CallPilot hackathon" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=critical,permitted;IP:172.20.10.0/255.255.255.240,permitted;DNS:invalid,permitted;email:invalid" \
    2>/dev/null
  chmod 600 "$CA_KEY"
fi

SANS=$(python3 -c 'print(",".join(f"IP:172.20.10.{i}" for i in range(2, 15)))')
openssl req -newkey rsa:2048 -nodes -keyout "$KEY" -out "$DIR/guest.csr" -subj "/CN=CallPilot guest (hotspot)" 2>/dev/null
printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\nbasicConstraints=CA:FALSE\n" "$SANS" > "$DIR/guest.ext"
openssl x509 -req -in "$DIR/guest.csr" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial -days 30 \
  -extfile "$DIR/guest.ext" -out "$CRT" 2>/dev/null
rm -f "$DIR/guest.csr" "$DIR/guest.ext"
chmod 600 "$KEY"
openssl verify -CAfile "$CA_CRT" "$CRT" >/dev/null && echo "Guest certificate issued for 172.20.10.2–14 (30 days)."

touch .env
python3 - "$IP" "$CRT" "$KEY" <<'PY'
import pathlib, sys
ip, cert, key = sys.argv[1:]
vals = {"GUEST_ENABLED": "true", "GUEST_BIND": ip, "GUEST_PORT": "8443", "GUEST_PUBLIC_DOMAIN": ip,
        "GUEST_TLS_CERT": cert, "GUEST_TLS_KEY": key}
p = pathlib.Path(".env")
lines = [l for l in p.read_text().splitlines() if l.split("=", 1)[0].strip() not in vals]
p.write_text("\n".join(lines + [f"{k}={v}" for k, v in vals.items()]) + "\n")
PY
echo ".env updated → guest links: https://$IP:8443/join/<token>"

if [ "${1:-}" = "--ca" ]; then
  echo
  echo "Optional, removes the Safari warning: AirDrop this file to the iPhone:"
  echo "  $CA_CRT"
  echo "Then on the iPhone: Settings → Profile Downloaded → Install;"
  echo "  Settings → General → About → Certificate Trust Settings → enable 'CallPilot Hotspot Demo CA'."
  echo "Remove it after the event: Settings → General → VPN & Device Management."
  open -R "$CA_CRT"
fi
