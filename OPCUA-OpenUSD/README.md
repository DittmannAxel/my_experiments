# Robot Digital Twin PoC

OPC UA → OpenUSD • Microsoft Agent Framework • vLLM • NVIDIA Omniverse Kit.

See [BUILD.md](BUILD.md) for the full build specification.

## Quick start (host: `192.168.21.230`)

```bash
cd ~/dev/git/my_experiments/OPCUA-OpenUSD          # path on the host
cp .env.example .env                                # then edit the changeme values
./scripts/gen-certs.sh                              # one-time: self-signed CA + leaf cert
docker compose up -d
./scripts/healthcheck.sh
```

Open `https://stack.local/` from the Mac (after adding `192.168.21.230  stack.local` to `/etc/hosts`).

## Host prerequisites (already provisioned on `Beasty`)

- Docker 29+, Docker Compose v2, NVIDIA Container Runtime registered
- 2× NVIDIA RTX 6000 Ada Generation (98 GB total VRAM)
- bare-metal vLLM serving `Qwen/Qwen3.6-35B-A3B` on `:8000` with
  `--enable-auto-tool-choice --tool-call-parser qwen3_xml --reasoning-parser qwen3`

The stack reaches vLLM via `http://host.docker.internal:8000/v1`
(`extra_hosts: host-gateway` pattern, see BUILD.md Trap 1).

## Phase status

- [x] Phase 0 — Scaffolding (Traefik + landing page)
- [ ] Phase 1 — OPC UA server + Node-RED dashboard
- [ ] Phase 2 — Bridge + USD authoring
- [ ] Phase 3 — InfluxDB + Telegraf + Grafana
- [ ] Phase 4 — Omniverse Kit App Streaming
- [ ] Phase 5 — pgvector + RAG-MCP
- [ ] Phase 6 — MAF agent
- [ ] Phase 7 — Polish & demo runbook

## GPU split

vLLM currently uses **both** GPUs (`--tensor-parallel-size 2`,
`--gpu-memory-utilization 0.93`). When Phase 4 (Omniverse Kit) is brought up,
vLLM must be stopped first to free GPU 1; the agent and RAG-MCP then talk to a
restarted vLLM single-GPU on GPU 0. See BUILD.md Trap 5.
