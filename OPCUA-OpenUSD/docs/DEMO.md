# Demo flow

A self-contained run-through. Total elapsed time from cold boot to first anomaly cycle: **~30 s** once the stack is healthy.

## Pre-flight (one-time per session)

On the GPU host (`192.168.21.230`):

1. **Bare-metal vLLM up.** Either Nemotron-3 (default in compose) or Qwen3.6-35B-A3B + DFlash. Confirm:
   ```bash
   curl -s http://127.0.0.1:8000/v1/models | jq '.data[0].id'
   ```
   If empty, start it: `~/launch_vllm_nemotron3_gpu0.sh` *or* `~/launch_vllm_qwen36_dflash.sh`. Match the served-model-name with `VLLM_MODEL` in `.env` / `docker-compose.yml`.

2. **Stack up.**
   ```bash
   cd ~/dev/git/my_experiments/OPCUA-OpenUSD
   docker compose up -d
   ./scripts/healthcheck.sh
   ```
   Expected: 13 services, all healthy except `omniverse-kit` and `telegraf` (no healthcheck defined; check by hand).

On your laptop, three browser tabs:

- **Dashboard:** `https://stack.local/dashboard/` — main operator UI (gauges, perf, alerts, agent panel).
- **3D view:** `http://stack.local:8082/` — Omniverse WebRTC stream (plain `http` is required; WebRTC signalling is `ws://`, mixed-content blocks it from `https://`).
- **Ask the Spec:** built into the dashboard.

`stack.local` resolves via your `/etc/hosts` to `192.168.21.230`.

## The live flow

Numbers in brackets are wall-clock from clicking **⚠ Inject**.

### 1. Baseline (Running, all clear)
- Dashboard `Status: Running` (green), `All systems normal`, gauges sweeping, motor temps ~22-28 °C, throughput climbing.
- 3D view: robot tracing the pick-and-place trajectory.

### 2. Click ⚠ **Inject** (header button) [t = 0]
- Triggers `axis4_overheat` via the OPC UA `InjectAnomaly` method.
- Axis-4 motor begins ramping toward 95 °C over 8 s.

### 3. Safety interlock fires [~t = 4 s]
- Motor temp crosses 90 °C → simulator auto-transitions `ProgramState 2 → 4`.
- Dashboard `Status` flips to **red `Stopped` / "Safety interlock — over-temperature; awaiting agentic recommendation"**.
- All gauges freeze at the position where the robot stopped.
- 3D view freezes (bridge keeps writing the same joint values).
- Throughput / efficiency tiles render as `—` / `Paused`.

### 4. Agent files recommendation [~t = 8 s]
- Anomaly detector observes temp ≥ 90 °C (first-crossing fire) → emits `AnomalyEvent`.
- `maf-agent` runs `agent.handle_anomaly()`: ~3-5 s with thinking-mode off.
- Dashboard alert tile flips to **`Agentic alert pending` / "MAF agent posted a recommendation — see panel below"**.
- New panel appears below: **`🤖 AGENTIC ALERT — recommendation from the MAF agent · awaiting operator approval`** with the agent's title, rationale (cites a Core/Part4 spec section), and the proposed actions.
- Motor temp is already cooling (anomaly clamp gated on `is_running`).

### 5. Click ✓ **Approve** (in the recommendation panel)
- Posts to `/api/approve?approved=true` → OPC UA method `ApproveRecommendation` parses the JSON and writes `ProgramState=6`.
- Status flips to **red `MaintenanceRequired` / "Agentic maintenance — operator must hit Reset to resume"**.
- Robot stays halted; motor continues to cool.
- Button label momentarily flips to `Approving…` for visible feedback (1.5 s settle).

### 6. Click ↻ **Reset** (header button)
- Posts to `/api/reset` → single OPC UA method `ResetMaintenance`:
  - Clears the active anomaly
  - Clears the active recommendation
  - Signals the simulator to thermal-reset
  - Writes `ProgramState=2`
- On the simulator's next tick: `motor_temp[]` baselined to 28 °C, `actual_temp[]` to 25 °C, robot resumes the trajectory.
- Dashboard `Status: Running` (green), gauges sweeping again, agentic-alert panel disappears.
- 3D view starts moving again.

### 7. (Optional) Ask the Spec
- Type a question in the lower-right panel, or click a preset chip:
  - *"How does OPC UA model a temperature with high and low alarm limits?"*
  - *"Which OPC UA alarm type signals a value exceeding a high limit?"*
  - *"What is NonExclusiveLimitAlarmType used for?"*
- The query routes through Traefik `/spec` → `rag-mcp` → pgvector retrieval → vLLM `chat.completions` with the chunks as context.
- Response arrives in 1-2 s with inline citations like `[Core/Part9#a770e560]`. The spinner shows live elapsed seconds; if the model takes too long, the request aborts after 90 s with a clear error.

## Repeat
- Cooldown on the agent is 30 s — wait that long between Inject cycles or the second one is silently debounced.
- Reset always clears everything; the demo is fully repeatable.

## Scripted version
`./scripts/demo-anomaly.sh` runs steps 1-4 from the host CLI and prints the recommendation JSON. Useful for smoke-testing the agent without a browser.
