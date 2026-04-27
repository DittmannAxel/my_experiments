#!/usr/bin/env bash
# End-to-end anomaly demo.
# 1. Confirm health, 2. baseline, 3. inject anomaly, 4. watch for recommendation.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

HOST="${STACK_HOSTNAME:-stack.local}"

echo "=== Axel Robot Twin Demo ==="
echo "1. Confirming all services healthy..."
./scripts/healthcheck.sh || true

echo
echo "2. Starting 30 s baseline observation period..."
sleep 30

echo
echo "3. Injecting anomaly: axis 4 motor overheat..."
docker compose exec -T opcua-server python -c "
import asyncio
from asyncua import Client, ua
async def main():
    async with Client('opc.tcp://localhost:4840/axel/robot') as c:
        ns = await c.get_namespace_index('urn:axel:robot')
        ctrl = await c.nodes.objects.get_child([f'{ns}:RobotController'])
        tc = await ctrl.get_child([f'{ns}:TaskControl'])
        await tc.call_method(f'{ns}:InjectAnomaly', ua.Variant('axis4_overheat', ua.VariantType.String))
        print('anomaly injected')
asyncio.run(main())
"

echo
echo "4. Watching for agent recommendation (max 90 s)..."
deadline=$(( $(date +%s) + 90 ))
RECO=""
while [[ $(date +%s) -lt $deadline ]]; do
    RECO=$(docker compose exec -T opcua-server python -c "
import asyncio
from asyncua import Client
async def main():
    async with Client('opc.tcp://localhost:4840/axel/robot') as c:
        ns = await c.get_namespace_index('urn:axel:robot:recommendations')
        n = c.get_node(f'ns={ns};s=RobotRecommendations.ActiveRecommendation')
        print(await n.read_value())
asyncio.run(main())
" || true)
    if [[ -n "$RECO" && "$RECO" != "" ]]; then
        echo "Got recommendation:"
        echo "$RECO" | python3 -m json.tool 2>/dev/null || echo "$RECO"
        break
    fi
    sleep 5
done

if [[ -z "$RECO" ]]; then
    echo "No recommendation within 90 s — check 'docker logs rt-agent'."
    exit 1
fi

echo
echo "5. Demo complete. Approve in the Robotics Dashboard at https://${HOST}/dashboard/"
