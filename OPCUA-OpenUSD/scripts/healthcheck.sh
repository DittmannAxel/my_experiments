#!/usr/bin/env bash
# Pings every service in the stack. Returns non-zero if any fails.
# Used by acceptance gates and the demo runbook.
set -uo pipefail

HOST="${STACK_HOSTNAME:-stack.local}"
fail=0

probe() {
  local name="$1" url="$2" expected="${3:-2..}"
  local code
  code=$(curl -s -k -o /dev/null -w "%{http_code}" -m 5 "$url" || echo 000)
  if [[ "$code" =~ ^${expected//\./.}$ ]]; then
    printf "  %-18s OK   (%s)\n" "$name" "$code"
  else
    printf "  %-18s FAIL (%s) → %s\n" "$name" "$code" "$url"
    fail=1
  fi
}

echo "Healthcheck — host=$HOST"
probe "landing"      "https://$HOST/"                    "2.."
probe "nodered"      "https://$HOST/nodered/"            "2..|3.."
probe "grafana"      "https://$HOST/grafana/api/health"  "2.."
probe "usd-signal"   "https://$HOST/usd/"                "2..|3..|4.."
probe "spec-http"    "https://$HOST/spec/health"         "2..|4.."
probe "opcua-tcp"    "tcp://$HOST:4840"                  "0.."   # filled in below

# Plain TCP probe for OPC UA — uses bash /dev/tcp.
if (echo > "/dev/tcp/${HOST}/4840") 2>/dev/null; then
  printf "  %-18s OK   (tcp open)\n" "opcua-tcp"
else
  printf "  %-18s FAIL (no tcp)\n" "opcua-tcp"
  fail=1
fi

# vLLM bare-metal on host — only checked from inside docker network or LAN.
probe "vllm"         "http://${HOST}:8000/v1/models"     "2.."

exit $fail
