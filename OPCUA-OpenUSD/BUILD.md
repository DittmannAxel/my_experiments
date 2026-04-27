# Robot Digital Twin PoC — Build Specification

> **Audience:** Claude Code (autonomous coding agent)
> **Owner:** Axel
> **Goal:** Buildable spec — read top-to-bottom, execute phase-by-phase, check acceptance gates before moving on.

---

## Mission

Build a containerized Industry-4.0 Proof-of-Concept on a Linux GPU host that demonstrates:

1. An OPC UA server (asyncua, Robotics Companion-Spec flavored) simulating a **6-axis pick-and-place robot**.
2. A **deterministic Python bridge** that maps OPC UA values → live updates of an OpenUSD stage.
3. **NVIDIA Omniverse Kit App Streaming** as the 3D viewer (browser-accessible via WebRTC, GPU-accelerated by the host's A6000s).
4. A **Microsoft Agent Framework (MAF)** advisory agent that monitors the OPC UA values, reasons about anomalies via vLLM (Nemotron on host), looks up OPC UA spec compliance via a local RAG-MCP server, and writes spec-compliant maintenance recommendations back to the OPC UA server with HITL approval.
5. **Operational tooling** — Node-RED dashboard for OPC UA, Grafana over InfluxDB for time-series, Traefik with self-signed TLS, a landing page that links everything.

The PoC must be **demoable end-to-end** in a single command: `docker compose up`.

---

## Target Host

- **Hostname / IP:** `192.168.21.230` (Ubuntu)
- **GPU:** 2× NVIDIA RTX A6000 (96 GB total VRAM)
- **vLLM:** Runs **bare-metal on the Ubuntu host** — NOT in a container, NOT managed by this compose stack. It serves an OpenAI-compatible API on `:8000`. This stack consumes it via `http://host.docker.internal:8000/v1` from inside containers (using the `host-gateway` extra_hosts pattern, see Trap 1).
- **vLLM model:** Nemotron variant (Axel will confirm exact name; discover at runtime via `/v1/models`).
- **Other prereqs already installed on the host:** Docker, Docker Compose v2, NVIDIA Container Toolkit (`nvidia-ctk`), CUDA drivers compatible with both vLLM (bare-metal) AND the Omniverse Kit container.
- **Mac client:** Axel's M-series Mac, used only as a thin client (browser).

---

## Stack — Pinned Versions

| Component | Version | Notes |
|---|---|---|
| Python (services) | 3.11 | All Python services use this |
| asyncua | latest stable | OPC UA server + client |
| pxr / usd-core | latest stable on PyPI | USD authoring |
| Microsoft Agent Framework | `agent-framework[anthropic,openai] --pre` | Use OpenAI provider pointed at vLLM |
| sentence-transformers | latest | mxbai-embed-large for embeddings |
| pgvector | 0.7+ | Postgres extension |
| PostgreSQL | 16 | base for pgvector |
| Node-RED | latest LTS image | quickwin dashboard |
| InfluxDB | 2.x OSS | time-series |
| Telegraf | latest | OPC UA → InfluxDB |
| Grafana | latest OSS | dashboards |
| Traefik | v3 | reverse proxy |
| Omniverse Kit App Template | latest from `NVIDIA-Omniverse/kit-app-template` | streaming viewer |

---

## Architecture

```
                            ┌─────────────────────────────────────────────────────┐
                            │  Browser on Mac                                      │
                            │  https://stack.local/  (self-signed TLS)             │
                            └────────────────┬────────────────────────────────────┘
                                             │
                            ┌────────────────▼────────────────────────────────────┐
                            │  Traefik :443                                        │
                            │  /            → landing-page                         │
                            │  /nodered     → Node-RED                             │
                            │  /grafana     → Grafana                              │
                            │  /usd         → omniverse-kit (HTTP+WS for signaling)│
                            │  /spec        → rag-mcp HTTP debug endpoint          │
                            └─────────┬──────────┬──────────┬──────────┬──────────┘
                                      │          │          │          │
                ┌─────────────────────┴────┐   ┌─┴──────┐   │      ┌───┴───────────┐
                │  opcua-server :4840       │   │node-red│   │      │ omniverse-kit │
                │  asyncua, Robotics 40010  │◄──┤ :1880  │   │      │ :8443 + UDP   │
                │  SignAndEncrypt           │   └────────┘   │      │ A6000 GPU     │
                └─────┬───────┬─────────────┘                │      └───────▲───────┘
                      │       ▲                              │              │
              monitored      writes back                     │       reads stage
              items (sub)    (recommendations)               │              │
                      │       │                              │      ┌───────┴────────┐
                ┌─────▼───────┴─────┐                        │      │ usd-stage      │
                │ bridge            │────writes USD layer────┼─────►│ (volume)       │
                │ async, deterministic                       │      │ stage.usda +   │
                └─────┬─────────────┘                        │      │ live.usda      │
                      │                                      │      └────────────────┘
                      └──────────────────┐                   │
                                         │                   │      ┌────────────────┐
                                ┌────────▼─────────┐         │      │  influxdb      │
                                │ telegraf         │─writes─►│      │  :8086         │
                                │ OPC UA→InfluxDB  │         │      └────────▲───────┘
                                └──────────────────┘         │               │
                                                             │               │ queries
                                                             │      ┌────────┴───────┐
                                ┌──────────────────┐         │      │  grafana :3000 │
                                │ maf-agent        │         │      └────────────────┘
                                │ subscribes OPCUA │         │
                                │ tools:           │         │
                                │  - write_recom   │         │
                                │  - spec_query    │         │
                                │  - usd_state     │         │
                                │ vLLM: Nemotron   │         │
                                └─────┬─────────┬──┘         │
                                      │         │            │
                              http to host    MCP/HTTP       │
                              vLLM :8000        │            │
                                      │         │            │
                                      ▼         ▼            │
                               ┌──────────┐ ┌─────────────┐  │
                               │  vLLM    │ │ rag-mcp     │  │
                               │ on host  │ │ :49322      │  │
                               │ Nemotron │ │ MCP/SSE+HTTP│  │
                               └──────────┘ └──────┬──────┘  │
                                                   │         │
                                            ┌──────▼──────┐  │
                                            │ pgvector    │  │
                                            │ :5432       │  │
                                            │ OPC UA spec │  │
                                            │ embeddings  │  │
                                            └─────────────┘  │
                                                             │
                            All services on internal compose network
                            Only Traefik exposes ports to host
```

### Service Inventory

| Service | Image / Build | Internal Port | Routed Path | GPU | Depends on |
|---|---|---|---|---|---|
| `traefik` | `traefik:v3` | 80, 443 | — | no | — |
| `landing-page` | local build | 80 | `/` | no | — |
| `opcua-server` | local build | 4840 | (not web) | no | — |
| `opcua-nodered` | `nodered/node-red:latest` | 1880 | `/nodered` | no | opcua-server |
| `bridge` | local build | — | — | no | opcua-server, usd-stage volume |
| `omniverse-kit` | local build (NVIDIA Kit App) | 8443, 47995-48005/udp | `/usd` | **yes** | usd-stage volume |
| `influxdb` | `influxdb:2` | 8086 | (not web; via grafana) | no | — |
| `telegraf` | `telegraf:latest` | — | — | no | opcua-server, influxdb |
| `grafana` | `grafana/grafana-oss:latest` | 3000 | `/grafana` | no | influxdb |
| `pgvector` | `pgvector/pgvector:pg16` | 5432 | — | no | — |
| `rag-mcp` | local build | 49322 | `/spec` | no (CPU embed is fine) | pgvector |
| `maf-agent` | local build | — | — | no | opcua-server, rag-mcp, vLLM (host) |

---

## Repository Layout — Authoritative

```
robot-twin-poc/
├── README.md
├── BUILD.md                          # ← this file
├── docker-compose.yml
├── .env.example
├── .gitignore
├── traefik/
│   ├── traefik.yml
│   └── certs/                         # generated, gitignored
├── landing-page/
│   ├── Dockerfile
│   └── index.html
├── opcua-server/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── certs/                         # generated, gitignored
│   ├── entrypoint.sh
│   └── src/
│       ├── server.py                  # asyncua server + main loop
│       ├── robotics_model.py          # OPC UA address space (Robotics flavor)
│       └── simulator.py               # joint motion + anomaly injection
├── opcua-nodered/
│   ├── Dockerfile                     # base image + opcua nodes pre-installed
│   ├── settings.js
│   └── flows.json                     # pre-built dashboard
├── bridge/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── src/
│       ├── main.py                    # async loop
│       ├── opcua_client.py            # subscribes monitored items
│       └── usd_writer.py              # mutates live.usda
├── usd-assets/
│   ├── README.md                      # describes layer composition
│   ├── stage.usda                     # root, sublayers cell + robot + live
│   ├── cell.usda                      # static cell geometry
│   ├── robot.usda                     # 6-axis rig with named joints
│   └── live.usda                      # initially empty; bridge writes here
├── omniverse-kit/
│   ├── Dockerfile
│   ├── README.md                      # follow kit-app-template, with deltas
│   └── app/                           # custom Kit extension that loads stage.usda
├── pgvector/
│   ├── Dockerfile                     # adds pgvector ext on init
│   └── init.sql
├── rag-mcp/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── ua-spec-source/                # git submodule of UA-for-AI-Prototype
│   └── src/
│       ├── init_db.py                 # one-shot: read rag-chunks.json, embed, upsert to pgvector
│       ├── embedder.py                # mxbai-embed-large via sentence-transformers
│       ├── retriever.py               # top-k cosine search in pgvector
│       ├── generator.py               # vLLM client (OpenAI-compat)
│       └── server.py                  # MCP/SSE on :49322 + HTTP /api/specification/query
├── maf-agent/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── prompts/
│   │   └── system.md
│   └── src/
│       ├── main.py
│       ├── tools.py                   # write_recommendation, query_specification, query_usd_state
│       └── agent.py                   # MAF agent wiring + HITL approval
├── influxdb/
│   └── (no custom build, just env in compose)
├── telegraf/
│   └── telegraf.conf                  # OPC UA input → InfluxDB output
├── grafana/
│   └── provisioning/
│       ├── datasources/influx.yaml
│       └── dashboards/
│           ├── dashboards.yaml
│           └── robot.json
└── scripts/
    ├── gen-certs.sh                   # mkcert / openssl for self-signed
    ├── seed-rag.sh                    # one-shot, runs rag-mcp init_db
    ├── demo-anomaly.sh                # injects anomaly into simulator
    └── healthcheck.sh                 # pings every service
```

---

## Critical Decisions — Do Not Deviate

1. **vLLM runs bare-metal on the host, not in this stack.** Do NOT add a vLLM container. Do NOT pull model weights inside any container in this repo. All services that need an LLM use `http://host.docker.internal:8000/v1` and the OpenAI-compatible API. In `docker-compose.yml`, every such service must have:
   ```yaml
   extra_hosts:
     - "host.docker.internal:host-gateway"
   ```
   Verify reachability before debugging anything LLM-related (see Trap 1).
2. **Embeddings happen in `rag-mcp` via sentence-transformers, NOT via vLLM and NOT via Ollama.** Model: `mixedbread-ai/mxbai-embed-large-v1`. CPU is sufficient for the one-shot init (~thousands of chunks) and for query-time embedding.
3. **The bridge is the ONLY component that writes to `live.usda`.** No agent, no other process. Determinism is sacred.
4. **The agent is advisory.** It writes recommendations to a SEPARATE OPC UA namespace (`urn:axel:robot:recommendations`) — never to the primary process variables. Anomaly response stays human-in-the-loop via MAF `approval_mode="always_require"`.
5. **All inter-service traffic stays on the compose network.** Only Traefik (and OPC UA + Omniverse UDP, see Phase 4) bind host ports.
6. **Self-signed cert for the WHOLE stack.** Generate one root CA, sign one wildcard for `*.stack.local`. Add `stack.local` to the host's `/etc/hosts` (and instruct user to add to Mac's `/etc/hosts`).
7. **OPC UA Server uses `SignAndEncrypt` with `Basic256Sha256`.** Even for PoC. Generate cert in `opcua-server/entrypoint.sh` if missing. Username/password auth: `axel` / set in `.env`.
8. **Use `docker compose` (v2), not `docker-compose`.** No legacy.
9. **Every service has a healthcheck.** No exceptions. `scripts/healthcheck.sh` verifies all green.
10. **Container logs go to stdout/stderr only.** No log files inside containers.

---

## Build Phases

Each phase is **independently demoable**. Do NOT advance until acceptance criteria pass.

### Phase 0 — Scaffolding

**Deliverable:** Repo skeleton, compose stub, Traefik with self-signed TLS, landing page reachable.

**Files to create:**
- `docker-compose.yml` (initially with just traefik + landing-page)
- `.env.example` with all variables documented
- `.gitignore` (certs, *.pyc, __pycache__, node_modules, .venv, etc.)
- `traefik/traefik.yml`
- `traefik/dynamic.yml` (file provider for TLS config)
- `scripts/gen-certs.sh` — uses `openssl` to make a CA + wildcard cert for `*.stack.local`
- `landing-page/Dockerfile` + `landing-page/index.html` — single HTML file with a list of links to each service (most will 502 until later phases). Use plain HTML, no framework.

**Acceptance:**
- [ ] `docker compose up -d` brings up Traefik and landing-page with no errors.
- [ ] `https://stack.local/` returns landing page over self-signed TLS (browser warns; acceptable).
- [ ] `scripts/healthcheck.sh` reports OK for both services.

---

### Phase 1 — OPC UA Server + Node-RED Dashboard

**Deliverable:** Running OPC UA server with simulated 6-axis robot, Node-RED dashboard showing live values.

#### Phase 1a: `opcua-server`

**Address space — minimum viable:**

```
Objects/
└── RobotController          (BaseObjectType)
    ├── Identification/
    │   ├── Manufacturer     (String)            "Axel Demo Robotics"
    │   ├── Model            (String)            "Demo6Axis-1"
    │   └── SerialNumber     (String)            "POC-001"
    ├── MotionDevice/
    │   ├── Axis1/
    │   │   ├── ActualPosition       (Double, deg)        [-180..180]
    │   │   ├── ActualSpeed          (Double, deg/s)
    │   │   ├── ActualTemperature    (Double, °C)         [20..120]
    │   │   └── MotorTemperature     (Double, °C)
    │   ├── Axis2/   (same children)
    │   ├── Axis3/   (same children)
    │   ├── Axis4/   (same children)
    │   ├── Axis5/   (same children)
    │   └── Axis6/   (same children)
    ├── Tool/
    │   ├── GripperState     (Boolean)            true=closed
    │   └── PayloadKg        (Double)
    ├── ProgramState         (Int32, PackML-flavored)  see enum below
    ├── CycleCounter         (UInt64)
    └── TaskControl/
        └── ResetMaintenance (Method, no args, returns StatusCode)

Objects/
└── RobotRecommendations    (BaseObjectType)        ← written by maf-agent
    ├── ActiveRecommendation (String, JSON-encoded)
    ├── RecommendationCount  (UInt32)
    └── ApproveRecommendation (Method, args: id String, approved Boolean)
```

**ProgramState enum:** `0=Idle, 1=Starting, 2=Running, 3=Stopping, 4=Stopped, 5=Aborted, 6=MaintenanceRequired`.

**Server config:**
- Endpoint URL: `opc.tcp://0.0.0.0:4840/axel/robot`
- Security policies: `None` (browse-only) AND `Basic256Sha256` with `SignAndEncrypt`
- User auth: anonymous (read-only) AND username+password (full)
- Self-signed app cert generated by `entrypoint.sh` if `/app/certs/server_cert.der` is missing

**Simulator (`simulator.py`):**
- 50 Hz update loop.
- All 6 axes drive a synthetic pick-and-place trajectory (lerp between waypoints + sinusoidal jitter).
- Temperatures drift slowly with motion energy (toy thermal model: `dT/dt = k * |speed| - c * (T - T_ambient)`).
- ANOMALY MODE: triggered by setting env `INJECT_ANOMALY=axis4_overheat`. Causes Axis4 motor temp to ramp from baseline → 95 °C over 60 s. The agent should detect this.
- ProgramState cycles through Running → Stopping → Idle every ~30 s during normal operation.

**Acceptance:**
- [ ] UaExpert (or any OPC UA client) can connect to `opc.tcp://192.168.21.230:4840/axel/robot` with the anonymous endpoint and browse the address space.
- [ ] All variables update at ≥10 Hz.
- [ ] Setting `INJECT_ANOMALY=axis4_overheat` causes Axis4 motor temp to climb visibly within 60 s.
- [ ] Method `ResetMaintenance` callable.

#### Phase 1b: `opcua-nodered`

**Approach:** Custom Dockerfile that pre-installs `node-red-contrib-opcua` and ships a `flows.json` so the dashboard is already built.

**Dashboard (Node-RED Dashboard 2.0):**
- Tab "Robot Live"
  - Group "Joints": 6 numeric gauges (one per axis position, units deg)
  - Group "Temperatures": 6 chart traces in a single chart (motor temps over last 5 min)
  - Group "Status": ProgramState as a colored pill (Running=green, Stopping=yellow, MaintenanceRequired=red, ...)
  - Group "Cycle": cycle counter as a big number
- Tab "Recommendations"
  - Active recommendation as a JSON viewer
  - Approve / Reject buttons → call `ApproveRecommendation` method on OPC UA server

**Connection config:**
- Endpoint: `opc.tcp://opcua-server:4840/axel/robot`
- Security: `None` for the Node-RED user (read-only views)
- For method calls (Approve), use the `axel` user with Basic256Sha256

**Acceptance:**
- [ ] `https://stack.local/nodered/` loads the dashboard.
- [ ] All 6 axis gauges show movement.
- [ ] Temperature chart shows live data.
- [ ] Triggering anomaly env makes Axis4 motor temp visibly ramp.

---

### Phase 2 — Bridge + USD Authoring

**Deliverable:** USD `live.usda` updates at ≥10 Hz, joints reflect OPC UA values. Validate by inspecting the `.usda` file or with `usdcat`.

#### `usd-assets/` static authoring (do this BEFORE writing the bridge)

**`robot.usda`** — define 6 named `Xform` prims, each with a known rotation axis. Use a simplified articulated chain — a cylinder per link is sufficient for the PoC. Geometry doesn't have to be beautiful, just legible.

Convention:
```
/World/Robot/Base/Joint1/Link1/Joint2/Link2/.../Joint6/Tool
```

Each `JointN` is an `Xform` with `xformOp:rotateXYZ`. Default rotation = (0,0,0). The bridge mutates these.

**`cell.usda`** — static cell geometry: floor plane, two stations (pickup + dropoff), a fence outline. Lighting: one DistantLight + one DomeLight with low intensity HDRI (PoC, free assets ok or just gray sky).

**`live.usda`** — starts with NO prims, just the layer header:
```usda
#usda 1.0
(
    defaultPrim = "World"
)
```
The bridge will write `over` defs into this layer.

**`stage.usda`** — root, with sublayers in the right order:
```usda
#usda 1.0
(
    defaultPrim = "World"
    upAxis = "Y"
    metersPerUnit = 1.0
    subLayers = [
        @./live.usda@,        # strongest — overrides win
        @./robot.usda@,
        @./cell.usda@
    ]
)
```

**`README.md` in `usd-assets/`** — explain the layer stack, axis conventions, and the convention that the bridge ONLY writes to `live.usda`.

#### `bridge/` Python service

**Behavior:**
- On startup: open `/stage/live.usda` as `Sdf.Layer`. If file doesn't exist, create with the empty header above.
- Open OPC UA client to `opcua-server:4840`, anonymous, monitor all 6 axes' `ActualPosition` and all 6 `MotorTemperature`s.
- On every batch of value updates (debounce to 50 ms windows), `Sdf.ChangeBlock`-wrap the writes:
  - For each axis, write an `over` on `/World/Robot/Base/Joint1/.../JointN` setting `xformOp:rotateXYZ` to (0, axis_value, 0) (or whichever axis matches that joint — document in `usd_writer.py`).
  - For temperatures: write a custom attribute or color override on the matching link (red shift as temp rises). Use `primvars:displayColor`.
- After batch: `layer.Save()`. Volume mount ensures Omniverse sees the change.

**Tuning:**
- Update batching: 50 ms windows is the right balance — fast enough for live feel, slow enough to avoid USD thrashing.
- If `Save()` becomes a bottleneck (it shouldn't at 50 ms), switch to `layer.ExportToString()` + atomic file replace.

**Acceptance:**
- [ ] `cat /var/lib/docker/volumes/.../live.usda` shows non-empty content with rotation values that change.
- [ ] `usdcat live.usda` outside the container shows valid layer.
- [ ] No more than 50 ms typical write latency from OPC UA value change to disk.

---

### Phase 3 — Time-Series Telemetry (Influx + Telegraf + Grafana)

**Deliverable:** Grafana dashboard reachable at `https://stack.local/grafana/`, showing live charts of all OPC UA values.

This phase intentionally comes BEFORE the Omniverse viewer — it's lower-risk and gives you a second confirmation that OPC UA → consumer flow works end-to-end.

#### `influxdb`
- Image: `influxdb:2`
- Env vars from `.env`:
  - `DOCKER_INFLUXDB_INIT_MODE=setup`
  - `DOCKER_INFLUXDB_INIT_USERNAME=admin`
  - `DOCKER_INFLUXDB_INIT_PASSWORD` from `.env`
  - `DOCKER_INFLUXDB_INIT_ORG=axel`
  - `DOCKER_INFLUXDB_INIT_BUCKET=robot`
  - `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN` from `.env` (generate once, document)

#### `telegraf`
- Custom `telegraf.conf` with:
  - `[[inputs.opcua_listener]]` (preferred) or `[[inputs.opcua]]` polling — listener is more efficient
  - Subscribes to all 12 axis variables + ProgramState + CycleCounter
  - Tags: `axis`, `metric_type`
  - Output: `[[outputs.influxdb_v2]]` to influxdb
- Flush interval: 1s

#### `grafana`
- Provisioned datasource pointing at influxdb (Flux query language)
- Provisioned dashboard `robot.json` with panels:
  - 6 time-series panels (axis positions)
  - 1 multi-series panel (motor temps overlaid, with threshold line at 90 °C)
  - 1 single-stat for cycle counter
  - 1 state-timeline for ProgramState
- Default time range: last 15 min, auto-refresh 5 s
- Anonymous viewing enabled (no login needed for the demo) — set `GF_AUTH_ANONYMOUS_ENABLED=true`, role `Viewer`, also set `GF_SERVER_ROOT_URL=https://stack.local/grafana/` and `GF_SERVER_SERVE_FROM_SUB_PATH=true`

**Acceptance:**
- [ ] `https://stack.local/grafana/` loads anonymously.
- [ ] All panels show live data updating every 5 s.
- [ ] Anomaly injection visible on motor-temp panel within 60 s.

---

### Phase 4 — Omniverse Kit App Streaming Viewer

**This is the hardest phase. Budget extra time.** Two paths; pick **A**, fall back to **B** only if A fails after solid attempts.

#### Path A (preferred): NVIDIA Kit App Template with WebRTC streaming

1. Clone `https://github.com/NVIDIA-Omniverse/kit-app-template` into `omniverse-kit/upstream/` (gitignored, or as submodule).
2. Follow the template's "Streaming" path. The template has a streaming reference app — start from that.
3. Customize the app's startup to:
   - Auto-load `/stage/stage.usda` from the mounted volume.
   - Set viewport camera to a fixed industrial-cell view of the robot.
   - Enable "live update" / file-watch on the stage so changes to `live.usda` reflect without manual reload. NVIDIA Kit has `omni.usd` APIs for this — typically `omni.client.live_set_default_enabled(True)` or `usd.UsdContext.reopen_stage()` on file change. Investigate with the template's docs.
4. Containerize via the template's Dockerfile, ensuring:
   - Base on `nvcr.io/nvidia/omniverse/...` (whatever the template uses).
   - `--gpus all` and `nvidia-container-runtime` in compose (`runtime: nvidia` and `deploy.resources.reservations.devices`).
   - Mount the `usd-stage` volume read-only at `/stage`.
   - Expose the streaming port (typically 8443 HTTPS for signaling) and the WebRTC UDP range (typically 47995–48005).
5. Behind Traefik: route `/usd` to the Kit container's HTTP signaling port. UDP streams direct to the host's published UDP range — Traefik does NOT proxy UDP.

**Compose snippet:**
```yaml
omniverse-kit:
  build: ./omniverse-kit
  runtime: nvidia
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
  volumes:
    - usd-stage:/stage:ro
  ports:
    - "47995-48005:47995-48005/udp"
  expose:
    - "8443"
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.usd.rule=Host(`stack.local`) && PathPrefix(`/usd`)"
    - "traefik.http.routers.usd.tls=true"
    - "traefik.http.services.usd.loadbalancer.server.port=8443"
```

#### Path B (fallback): `usdview` over noVNC

Only if Path A blocks for >2 days.

1. Build a container with: Pixar OpenUSD prebuilt binaries (NVIDIA's distribution from `developer.nvidia.com/usd`), TigerVNC, noVNC, websockify.
2. Auto-start `usdview /stage/stage.usda --autoLoad` on container boot.
3. Expose noVNC web client at `:6080`, route through Traefik at `/usd`.
4. GPU acceleration via `nvidia-container-toolkit`.

This is uglier but reliable. Document in the README that this is a temporary fallback.

**Acceptance (either path):**
- [ ] `https://stack.local/usd` loads a 3D view in the browser.
- [ ] Robot model is visible.
- [ ] Joint rotations from OPC UA reflect within ≤500 ms in the viewer.
- [ ] Color shift on overheating axis is visible during anomaly demo.

---

### Phase 5 — pgvector + RAG-MCP Server

**Deliverable:** A Python MCP server that exposes the `specificationQuery` tool over MCP-SSE on port 49322, plus a debug HTTP endpoint `POST /api/specification/query`.

#### `pgvector`
- Image: `pgvector/pgvector:pg16`
- Init SQL creates schema:
  ```sql
  CREATE EXTENSION IF NOT EXISTS vector;
  CREATE TABLE IF NOT EXISTS spec_chunks (
      id BIGSERIAL PRIMARY KEY,
      part TEXT NOT NULL,            -- e.g., "Core/Part4"
      chunk_id TEXT NOT NULL,
      title TEXT,
      content TEXT NOT NULL,
      embedding vector(1024)         -- mxbai-embed-large = 1024 dims
  );
  CREATE INDEX ON spec_chunks USING hnsw (embedding vector_cosine_ops);
  CREATE INDEX ON spec_chunks (part);
  ```

#### `rag-mcp`

**Source the OPC UA spec content:**
- Add `https://github.com/OPCFoundation/UA-for-AI-Prototype` as a git submodule under `rag-mcp/ua-spec-source/`.
- The data we need is `ua-spec-source/specifications/**/rag-chunks.json` — these are pre-tokenized chunks ready to embed.

**`init_db.py` — runs once on first startup:**
1. Connect to pgvector.
2. If `spec_chunks` already has rows: log "DB already populated, skipping" and return.
3. Walk `ua-spec-source/specifications/`, find all `rag-chunks.json`.
4. For each chunk: call `embedder.embed(chunk.content)`.
5. Bulk-insert into `spec_chunks`.
6. Expected count: a few thousand chunks. Should complete in 5–15 min on CPU.

**`embedder.py`:**
```python
from sentence_transformers import SentenceTransformer
_model = SentenceTransformer("mixedbread-ai/mxbai-embed-large-v1")
def embed(text: str) -> list[float]:
    return _model.encode(text, normalize_embeddings=True).tolist()
```
Cache the model once at import time.

**`retriever.py`:** top-k cosine search.
```python
def retrieve(query: str, k: int = 6, part_filter: str | None = None) -> list[Chunk]:
    qvec = embedder.embed(query)
    # SELECT ... ORDER BY embedding <=> %s LIMIT %s
    # Optional part LIKE filter
```

**`generator.py`:** vLLM client via `openai` SDK.
```python
from openai import OpenAI
_client = OpenAI(base_url="http://host.docker.internal:8000/v1", api_key="not-used")
def answer(question: str, chunks: list[Chunk]) -> str:
    context = "\n\n".join(f"[{c.part}#{c.chunk_id}] {c.content}" for c in chunks)
    # Compose user prompt: "Answer using only the spec excerpts below. Cite [Part#chunk_id]."
    resp = _client.chat.completions.create(
        model=os.environ["VLLM_MODEL"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
        ],
        temperature=0.1,
        max_tokens=600,
    )
    return resp.choices[0].message.content
```

**`server.py` — MCP server:**
- Use the official `mcp` Python SDK.
- Transport: SSE on port 49322 (also accept stdio for local debugging).
- Expose ONE tool: `specificationQuery`
  - input schema: `{"question": str, "part_filter": str | None}`
  - output: `{"answer": str, "citations": [{"part": str, "chunk_id": str, "snippet": str}]}`
- Also expose a plain HTTP route `POST /api/specification/query` with same schema (for curl debugging via Traefik).

**Compose:**
```yaml
rag-mcp:
  build: ./rag-mcp
  environment:
    - PGVECTOR_DSN=postgresql://rag:rag@pgvector:5432/rag
    - VLLM_BASE_URL=http://host.docker.internal:8000/v1
    - VLLM_MODEL=${VLLM_MODEL}
  extra_hosts:
    - "host.docker.internal:host-gateway"
  depends_on:
    pgvector:
      condition: service_healthy
  expose:
    - "49322"
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.spec.rule=Host(`stack.local`) && PathPrefix(`/spec`)"
    - "traefik.http.services.spec.loadbalancer.server.port=49322"
```

**Acceptance:**
- [ ] On first up: container takes 5–15 min to populate DB, then becomes healthy.
- [ ] On subsequent ups: ready within seconds.
- [ ] `curl -k -X POST https://stack.local/spec/api/specification/query -d '{"question":"How do I model a maintenance state in OPC UA Robotics?"}'` returns JSON with answer and citations.
- [ ] MCP SSE endpoint connectable from a separate test client.

---

### Phase 6 — MAF Agent

**Deliverable:** A long-running Python agent that observes OPC UA values, detects anomalies, queries the spec via MCP, and writes recommendations back with HITL approval.

#### Agent loop architecture

NOT chat-driven. The agent is event-driven:
1. Subscribe to OPC UA monitored items (motor temperatures, ProgramState).
2. Run a lightweight **anomaly detector** in pure Python (e.g., temperature exceeds threshold for >10 s, or rate-of-change too high).
3. When anomaly fires → invoke the LLM-backed **reasoning agent** with the anomaly context. The agent has tools.
4. Agent produces a recommendation; calls `write_recommendation_to_opcua` tool which:
   - Requires HITL approval (`approval_mode="always_require"` on the tool).
   - In headless mode, "approval" arrives via the Node-RED dashboard's Approve button (which calls `ApproveRecommendation` on the OPC UA server, which sets a flag the bridge polls).
5. Logs everything to stdout in structured JSON.

#### Tools

```python
@tool(approval_mode="never_require")
async def query_specification(question: str, part_filter: str | None = None) -> dict:
    """Query the OPC UA specification corpus for guidance on standard-compliant modeling."""
    # HTTP call to rag-mcp container's /api/specification/query
    ...

@tool(approval_mode="never_require")
async def query_usd_state(prim_path: str) -> dict:
    """Read the current value of a USD attribute (for reasoning about visual state)."""
    # opens /stage/live.usda read-only, queries the prim
    ...

@tool(approval_mode="always_require")
async def write_recommendation_to_opcua(
    title: Annotated[str, "Short title"],
    rationale: Annotated[str, "Why this is recommended"],
    actions: Annotated[list[dict], "List of {node_id, value} pairs the operator should approve"],
    spec_citation: Annotated[str | None, "Spec part/section if applicable"]
) -> str:
    """Publish a recommendation to OPC UA RobotRecommendations namespace."""
    # asyncua client write
    ...
```

#### Agent prompt (`prompts/system.md`)

```
You are an OT advisory agent for a 6-axis industrial robot exposed via OPC UA.

You observe live telemetry. When an anomaly is reported, you:
1. Use `query_specification` to find the OPC-UA-standard-compliant way to signal
   the issue (e.g., DI MaintenanceState, AlarmCondition types).
2. Compose a concrete recommendation with a node path, value, and a citation
   to the OPC UA Part/section.
3. Call `write_recommendation_to_opcua` with the structured action.

You NEVER write directly to process variables (axis positions, temperatures).
You ONLY publish recommendations to the RobotRecommendations namespace.

Recommendations require operator approval before they take effect.
```

#### LLM client wiring

```python
from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient

client = OpenAIChatClient(
    base_url=os.environ["VLLM_BASE_URL"],
    api_key="not-used",
    model=os.environ["VLLM_MODEL"],
)
agent = Agent(client=client, instructions=open("prompts/system.md").read(), tools=[...])
```

#### Anomaly detector (separate from the agent)

```python
class TemperatureAnomalyDetector:
    """Pure-Python rule-based detector. Runs in the asyncua subscription handler."""
    THRESHOLD_C = 90.0
    DURATION_S = 10.0
    # tracks last_above_threshold timestamp per axis; fires when held for DURATION_S
```

When it fires, push an event to an `asyncio.Queue`. The agent main loop awaits the queue and invokes the agent with a structured prompt:
```
ANOMALY DETECTED:
- axis: 4
- metric: motor_temperature
- value: 92.3 C
- threshold: 90.0 C
- duration_above: 12.4 s

Please investigate and recommend a standard-compliant action.
```

**Acceptance:**
- [ ] On `INJECT_ANOMALY=axis4_overheat`: within ~75 s, a recommendation appears in the OPC UA `RobotRecommendations.ActiveRecommendation` node, visible in Node-RED.
- [ ] The recommendation cites a specific OPC UA spec section (verify reasonable in logs).
- [ ] Approving via Node-RED triggers the recommended write (e.g., setting ProgramState=MaintenanceRequired).
- [ ] The bridge picks up the new ProgramState and the omniverse view colors something differently (e.g., a status indicator pad in the cell turns red).

---

### Phase 7 — Polish & Demo Runbook

**Deliverable:** A `scripts/demo-anomaly.sh` that runs the full anomaly story start-to-finish.

#### Landing page final form

Update `landing-page/index.html` to include:
- Big title: "Axel Robot Twin PoC"
- Health badges (poll healthchecks via fetch every 5 s)
- Quick links:
  - 🤖 Node-RED Dashboard (`/nodered/`)
  - 📊 Grafana (`/grafana/`)
  - 🌐 Omniverse 3D View (`/usd/`)
  - 📚 Spec Query API (`/spec/api/specification/query`)
- One "Inject Anomaly" button that calls a tiny endpoint on `opcua-server` to flip the env (or call a method on the server) — quality-of-life for live demos

#### `scripts/demo-anomaly.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Axel Robot Twin Demo ==="
echo "1. Confirming all services healthy..."
./scripts/healthcheck.sh

echo "2. Starting baseline observation period (30 s)..."
sleep 30

echo "3. Injecting anomaly: axis 4 motor overheating..."
docker compose exec opcua-server python -m src.simulator inject_anomaly axis4_overheat

echo "4. Watching for agent recommendation (max 90 s)..."
# poll the OPC UA server's ActiveRecommendation node for non-empty value
# print result

echo "5. Demo complete. Approve via Node-RED at https://stack.local/nodered/"
```

#### `README.md` final form

Top-level README should include:
- One-screenshot architecture
- "Quick start" section: `./scripts/gen-certs.sh && cp .env.example .env && docker compose up -d` then wait
- Troubleshooting section covering the known traps (below)
- Demo script invocation

---

## Known Traps & Solutions

### Trap 1: vLLM not reachable from containers

**Context:** vLLM runs **bare-metal on the host** (systemd unit, tmux session, or however Axel started it). Containers in this stack must reach it via the Docker bridge, not localhost.

**Symptom:** `maf-agent` or `rag-mcp` logs `Connection refused` or DNS errors for `host.docker.internal`.

**Cause:** Linux Docker doesn't auto-resolve `host.docker.internal` like Mac/Windows do.

**Fix:** EVERY service that needs vLLM must include in its compose service:
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

**Verification ladder** (run in order, fix at the first failure):
1. On host: `curl http://localhost:8000/v1/models` → should list the model.
2. On host: `curl http://192.168.21.230:8000/v1/models` → same; confirms vLLM is bound to all interfaces (NOT just 127.0.0.1). If this fails, vLLM was started with `--host 127.0.0.1`; needs `--host 0.0.0.0`.
3. In container: `docker compose exec maf-agent curl http://host.docker.internal:8000/v1/models` → confirms host-gateway works.
4. Check host firewall: `sudo ufw status` — port 8000 must be reachable from the Docker bridge interface (`docker0` or compose-network bridge).

### Trap 2: Tool calling support in vLLM

vLLM needs explicit flags to enable OpenAI-compatible tool calling. The bare-metal vLLM serving Nemotron must be started with at minimum:
- `--enable-auto-tool-choice`
- `--tool-call-parser <appropriate-for-Nemotron>` (e.g., `hermes` or a Nemotron-specific parser depending on the exact variant)

**This is a host-side concern, NOT something this repo controls** — but the top-level README must document the required flags so Axel can verify his vLLM systemd unit / tmux command. If tool-calling fails:
1. Confirm via `curl http://host.docker.internal:8000/v1/chat/completions -d '{"model":"...","messages":[...],"tools":[...]}'` returns a `tool_calls` field.
2. If not, restart vLLM with the right parser flag.
3. Only then debug the `agent-framework` openai client config.

### Trap 3: USD live reload in Omniverse

Omniverse Kit doesn't always auto-reload changes to a sublayer file unless explicitly told. Two options:
1. **File watch in the Kit app** — register an observer on the file, call `omni.usd.get_context().reopen_stage()` on change.
2. **Force-reload on a timer** — call `Sdf.Layer.Reload()` on the live sublayer every 100 ms.

Pick option 2 if option 1 proves fragile. It's slightly less elegant but more robust.

### Trap 4: pgvector init takes "forever"

Embedding the entire OPC UA spec corpus on CPU takes 5–15 min. Make sure the `rag-mcp` healthcheck only goes green AFTER `init_db.py` completes. Show progress to stdout (`logging.INFO` per spec part processed) so it doesn't look hung.

### Trap 5: GPU contention between bare-metal vLLM and Omniverse Kit container

vLLM is already running bare-metal and consumes one or both A6000s. The `omniverse-kit` container ALSO needs a GPU. Two scenarios:

- **Recommended split:** vLLM pinned to GPU 0, Kit container pinned to GPU 1.
  - Start vLLM with `CUDA_VISIBLE_DEVICES=0` in its environment, plus `--tensor-parallel-size 1 --gpu-memory-utilization 0.85`.
  - Pin the Kit container to GPU 1 with:
    ```yaml
    omniverse-kit:
      environment:
        - NVIDIA_VISIBLE_DEVICES=1
      deploy:
        resources:
          reservations:
            devices:
              - driver: nvidia
                device_ids: ["1"]
                capabilities: [gpu]
    ```
- **Shared GPUs:** if vLLM uses both A6000s for tensor-parallel inference, the Kit container will share. Works on RTX A6000 (no MIG required) but may add latency to inference. Prefer the split.

Document the chosen split in the README. Confirm with `nvidia-smi` on the host and `docker compose exec omniverse-kit nvidia-smi` inside the container.

### Trap 6: Node-RED behind Traefik path-prefix

Node-RED needs to know it's behind a path. Set in `settings.js`:
```js
httpAdminRoot: "/nodered",
httpNodeRoot: "/nodered/api",
ui: { path: "/nodered/ui" }
```
And the Traefik label uses a `stripPrefix` middleware:
```yaml
- "traefik.http.middlewares.nodered-strip.stripprefix.prefixes=/nodered"
- "traefik.http.routers.nodered.middlewares=nodered-strip"
```
Wait — Node-RED handles its own prefix when `httpAdminRoot` is set. So DON'T strip prefix; just route. Verify with both options if one fails.

### Trap 7: OPC UA self-signed cert exchange

asyncua server: on first connect from any client (including the bridge, telegraf, Node-RED), the client's cert lands in `/app/certs/trusted/` but starts as untrusted. Either:
- Pre-generate all client certs and trust them in the entrypoint, OR
- Set the server to auto-accept on first connect (insecure but acceptable for PoC).

Recommended: auto-accept for PoC. Set explicitly via asyncua server config and document.

### Trap 8: WebRTC behind Traefik

Traefik proxies HTTP and WebSocket fine. WebRTC media streams use UDP and bypass Traefik. The Kit container must publish the UDP range directly on the host. The browser connects to `https://stack.local/usd` for signaling, then negotiates UDP directly to `192.168.21.230:47995-48005`. From the Mac, that requires the Mac being on the same LAN — confirm.

---

## Demo Runbook

What Axel should be able to demo in 5 minutes:

1. **Open `https://stack.local/`** — landing page shows all services green.
2. **Open the Omniverse view (`/usd/`)** — robot is moving through pick-and-place trajectory.
3. **Open Node-RED (`/nodered/`)** — dashboard shows live values, all 6 axes ticking.
4. **Open Grafana (`/grafana/`)** — same data as time-series.
5. **Click "Inject Anomaly"** on the landing page (or run `./scripts/demo-anomaly.sh`).
6. **Watch axis 4's motor temp climb** in Grafana and Node-RED.
7. **Within ~75 s**, the agent posts a recommendation visible in Node-RED's "Recommendations" tab. The recommendation reads something like:
   > "Axis 4 motor temperature exceeded 90°C for 12s. Per OPC UA Robotics 40010 §X, set ProgramState=MaintenanceRequired and surface a MaintenanceRequiredAlarmType. Cite: Core/Part9, DI/v105."
8. **Operator clicks Approve** in Node-RED.
9. **The OPC UA server's ProgramState flips to 6 (MaintenanceRequired).**
10. **In the Omniverse view**, a status pad in the cell turns red, axis 4's link turns orange (from temperature gradient).
11. **Story closes:** "agent reasoned spec-compliantly, operator stayed in the loop, the digital twin reflected the action — all on local hardware."

---

## Acceptance — Whole-System

Before declaring "done":

- [ ] `docker compose up -d` from clean state brings everything up; all services healthy within 20 min (RAG init dominates first-run time).
- [ ] All 5 web UIs reachable from the Mac browser via `https://stack.local/...`.
- [ ] Demo runbook executes without manual intervention through step 7.
- [ ] Approval flow (steps 8–10) works end-to-end.
- [ ] Logs are clean (no recurring errors, no warning floods).
- [ ] Stopping the stack (`docker compose down`) is graceful; restarting (`up -d`) preserves state (volumes for usd-stage, pgvector, influxdb, grafana).

---

## What This PoC Intentionally Does NOT Include

To keep scope crisp:

- No Nucleus / Live-Sync — file-watch is sufficient for one-host demo.
- No real OPC UA cert PKI — self-signed only.
- No full PackML state machine — simplified ProgramState enum is sufficient.
- No multi-robot, no fleet, no MES integration — scope = single cell.
- No k3s — Docker Compose only.
- No CI/CD — local docker compose.
- No production-grade observability — Loki/Promtail can be added later if needed.

If Axel wants any of these, they're explicitly Phase 8+ (out of scope for this build).

---

## Open Questions (ask Axel before starting)

1. **Exact vLLM model name** as currently served — discover via `curl http://192.168.21.230:8000/v1/models` and write into `.env` as `VLLM_MODEL`.
2. **Confirm UA-for-AI-Prototype repo is reachable** for git submodule (it's public, should be fine).
3. **Cert hostname** — is `stack.local` ok, or does Axel prefer something else?
4. **GPU split** — confirm vLLM uses GPU 0 only, leave GPU 1 for Omniverse Kit.

---

## Done = User Can Demo

The North Star: Axel runs `docker compose up -d` on `192.168.21.230`, opens his Mac browser, navigates to `https://stack.local/`, and within 5 minutes shows a Mittelstand customer a credible OT-AI integration demo. Everything else is detail.
