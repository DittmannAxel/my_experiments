# Robot Digital Twin PoC

A containerized Industry-4.0 demo on a single GPU host that wires:

- **OPC UA server** (asyncua) simulating a 6-axis pick-and-place robot
- **OpenUSD bridge** that maps live OPC UA values → joint rotations + thermal-tinted links
- **Node-RED dashboard** (Dashboard 2.0) for live values + recommendation approval
- **InfluxDB / Telegraf / Grafana** time-series telemetry stack
- **pgvector + RAG-MCP** server backed by the official OPC UA spec corpus
- **Advisory agent** that subscribes anomaly events, queries the spec via RAG, and
  publishes spec-cited recommendations back to OPC UA with HITL approval
- **NVIDIA Omniverse Kit** browser-streamed 3D viewer (Phase 4 — wires up next)

All services are reverse-proxied by Traefik with one self-signed wildcard cert.

See [BUILD.md](BUILD.md) for the full build specification.

## Quick start

```bash
cd ~/dev/git/my_experiments/OPCUA-OpenUSD          # path on the host
cp .env.example .env                                # then fill in any 'changeme' values
./scripts/gen-certs.sh                              # one-time: self-signed CA + leaf cert
docker compose up -d                                # ≈10–15 min on first run (RAG embedding)
./scripts/healthcheck.sh                            # all green?
./scripts/demo-anomaly.sh                           # walk the full anomaly story
```

Then open `https://stack.local/` (after adding `192.168.21.230  stack.local` to your
Mac's `/etc/hosts`).

## URL map

| Path | Service |
|---|---|
| `/` | Landing page (this) |
| `/nodered/` | Node-RED admin |
| `/nodered/dashboard/` | Operator dashboard (Dashboard 2.0) |
| `/grafana/` | Time-series dashboards |
| `/spec/health` | RAG-MCP health |
| `/spec/api/specification/query` | RAG-MCP HTTP API |
| `/usd/` | Omniverse Kit viewer (Phase 4) |
| `opc.tcp://stack.local:4840/axel/robot` | OPC UA endpoint (4840 also exposed on host) |

## Host prerequisites (provisioned on `Beasty` / `192.168.21.230`)

- Docker 29.4+, Docker Compose v2, NVIDIA Container Runtime registered
- 2× NVIDIA RTX 6000 Ada Generation (98 GB total VRAM)
- bare-metal vLLM serving `Qwen/Qwen3.6-35B-A3B` on `:8000` with the flags
  `--enable-auto-tool-choice --tool-call-parser qwen3_xml --reasoning-parser qwen3`

Containers reach vLLM via `http://host.docker.internal:8000/v1`
(BUILD.md Trap 1: `extra_hosts: host-gateway`).

## Phase status

- [x] Phase 0 — Scaffolding (Traefik + landing page)
- [x] Phase 1a — OPC UA server (asyncua, simulator, anomaly injection)
- [x] Phase 1b — Node-RED dashboard
- [x] Phase 2 — Bridge + USD authoring
- [x] Phase 3 — InfluxDB + Telegraf + Grafana
- [x] Phase 5 — pgvector + RAG-MCP
- [x] Phase 6 — MAF agent (advisory, OpenAI tool-calling against vLLM)
- [ ] Phase 4 — Omniverse Kit App Streaming  (requires vLLM shutdown to free GPU 1)
- [x] Phase 7 — Polish, demo runbook, healthchecks

## GPU split

vLLM currently uses **both** GPUs (`--tensor-parallel-size 2`,
`--gpu-memory-utilization 0.93`). When Phase 4 (Omniverse Kit) is brought up,
vLLM must be stopped first to free GPU 1; the agent and RAG-MCP then talk to a
restarted vLLM single-GPU on GPU 0. See BUILD.md Trap 5.

## Layout

```
OPCUA-OpenUSD/
├── docker-compose.yml          # all services
├── .env.example                # environment template
├── traefik/                    # Traefik static + dynamic config
├── landing-page/               # nginx-served entry page
├── opcua-server/               # asyncua robot simulator
├── opcua-nodered/              # custom Node-RED image with pre-built flows
├── bridge/                     # OPC UA → USD authoring service
├── usd-assets/                 # stage.usda, robot.usda, cell.usda, live.usda
├── telegraf/                   # OPC UA → InfluxDB
├── grafana/provisioning/       # datasource + dashboard
├── pgvector/                   # pgvector pg16 + init.sql
├── rag-mcp/                    # FastAPI + sentence-transformers + MCP-SSE
├── maf-agent/                  # anomaly-driven advisory agent
└── scripts/
    ├── gen-certs.sh            # one-time CA + leaf cert
    ├── healthcheck.sh          # ping every service
    └── demo-anomaly.sh         # full anomaly demo
```

## Demo runbook

See `scripts/demo-anomaly.sh`. Summary:

1. **Open `https://stack.local/`** — landing page shows all services green.
2. **Click "Inject Anomaly"** (or run `./scripts/demo-anomaly.sh` on the host).
3. **Watch axis 4's motor temp climb** in Grafana / Node-RED dashboard.
4. **Within ~75 s** the agent posts a recommendation to OPC UA citing the spec.
5. **Click Approve in Node-RED** → the OPC UA server applies the recommended
   action (e.g. `ProgramState=6` MaintenanceRequired).
6. The bridge propagates that state into `live.usda`, where the Omniverse view
   (Phase 4) will reflect it visually as a red status pad.

## Troubleshooting

- **502 Bad Gateway on `/spec/...`** — RAG-MCP is still embedding the spec corpus
  on first boot (5–15 min). `docker logs rt-rag-mcp` shows progress.
- **Traefik 404 on a new path** — restart Traefik (`docker restart rt-traefik`);
  the file-watcher occasionally misses edits to `traefik/dynamic.yml`.
- **`scripts/healthcheck.sh` flakes on first run** — re-run after services finish
  startup (Grafana provisioning takes a moment).
