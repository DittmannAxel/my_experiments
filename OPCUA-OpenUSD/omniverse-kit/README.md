# Omniverse Kit App Streaming — Phase 4 (DEFERRED)

This directory holds the Omniverse Kit App container that streams the live
robot-cell USD stage to a browser via WebRTC.

## Status

**Deferred.** The other 8 services in the stack are working end-to-end and
demoable today; Phase 4 needs a meaningful chunk of additional work plus
careful GPU coordination with bare-metal vLLM.

## What's needed

Two paths from BUILD.md, in order of preference:

### Path A — NVIDIA Kit App Template (preferred)

1. Clone `https://github.com/NVIDIA-Omniverse/kit-app-template` into `upstream/`
   (gitignored).
2. Follow the template's "Streaming" example.
3. Customize `app/` to:
   - Auto-load `/stage/stage.usda` from the mounted volume.
   - Set the viewport camera to a fixed industrial-cell view of the robot.
   - File-watch `live.usda` so bridge writes reflect without manual reload —
     `omni.client.live_set_default_enabled(True)` or a `Sdf.Layer.Reload()`
     timer (BUILD.md Trap 3).
4. Containerize via the template's Dockerfile (NVIDIA base image + nvidia
   runtime). Mount the `usd-stage` volume at `/stage:ro`.
5. Expose port 8443 for the streaming signaling (HTTP + WebSocket) plus the
   WebRTC UDP range (47995–48005).

### Path B — usdview over noVNC (fallback)

1. Build a container with: NVIDIA OpenUSD prebuilt binaries
   (`developer.nvidia.com/usd`), TigerVNC, noVNC, websockify.
2. Auto-start `usdview /stage/stage.usda --autoLoad` on boot.
3. Expose noVNC at `:6080`, route via Traefik at `/usd`.
4. GPU acceleration via `nvidia-container-toolkit`.

## GPU coordination with vLLM

If vLLM is using both GPUs (tensor-parallel) it will need to be stopped
and restarted single-GPU before bringing up Omniverse Kit. Suggested order:

```bash
# 1. Stop vLLM
pkill -f 'vllm serve'

# 2. Restart vLLM single-GPU on GPU 0 only (--tensor-parallel-size 1)
CUDA_VISIBLE_DEVICES=0 ./launch_vllm_singlegpu.sh

# 3. Bring up the Kit container pinned to GPU 1
docker compose up -d omniverse-kit
```

The compose service must include:

```yaml
omniverse-kit:
  build: ./omniverse-kit
  runtime: nvidia
  environment:
    - NVIDIA_VISIBLE_DEVICES=1
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            device_ids: ["1"]
            capabilities: [gpu]
  volumes:
    - usd-stage:/stage:ro
  ports:
    - "47995-48005:47995-48005/udp"
  expose:
    - "8443"
```

And in `traefik/dynamic.yml`:

```yaml
http:
  routers:
    usd:
      rule: "Host(`stack.local`) && PathPrefix(`/usd`)"
      service: usd
      priority: 100
      entryPoints: [websecure]
      tls: {}
  services:
    usd:
      loadBalancer:
        servers:
          - url: "http://omniverse-kit:8443"
```

## Acceptance gate (when Path A or B lands)

- [ ] `https://stack.local/usd` loads a 3D view in the browser
- [ ] Robot model is visible
- [ ] Joint rotations from OPC UA reflect within ≤500 ms in the viewer
- [ ] Color shift on overheating axis is visible during anomaly demo
