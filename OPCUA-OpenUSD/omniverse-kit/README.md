# Omniverse Kit App Streaming — Phase 4 (Robot Twin Viewer)

This directory holds the wiring for the **Robot Twin Viewer**, a customized
NVIDIA Omniverse Kit App that loads `/stage/stage.usda` from the shared
`usd-stage` Docker volume and streams the resulting 3D viewport to a Mac
browser via WebRTC.

The browser entrypoint is **`https://stack.local/usd/`** (Traefik proxies the
HTTP signaling channel; WebRTC media goes UDP-direct to the host on
`47995-48005/udp`).

## Status

Wired into the compose stack. The image must be built from the upstream
`kit-app-template` checkout — see [Build](#build) below. Until that is done,
`docker compose up -d omniverse-kit` will fail to find the image
`axel/robot_twin_viewer:0.1.0`.

## Architecture

```
Mac browser  ──HTTPS──▶ Traefik (stack.local)  ──HTTP──▶ omniverse-kit:8011 (signaling)
        ▲                                                         │
        └────────────────────  WebRTC UDP 47995-48005  ◀──────────┘
                                  (direct to host)
```

The Kit container is pinned to **GPU 1** via `NVIDIA_VISIBLE_DEVICES=1` and
the matching compose `device_ids: ["1"]` reservation. **GPU 0 is reserved
for vLLM** (Nemotron Nano 8B) and must not be touched.

The container mounts the `usd-stage` Docker volume read-only at `/stage`,
which is the same volume `bridge` writes `live.usda` into. The customized
setup extension polls `live.usda` on a 250 ms loop (`Sdf.Layer.Reload(force=True)`)
so OPC UA → bridge → USD updates surface in the viewer with sub-second latency.

## Files in this directory

| File | Purpose |
|---|---|
| `Dockerfile` | Fallback image build that wraps the kit-app-template `_build/linux-x86_64/release` tree on top of `nvcr.io/nvidia/omniverse/ov-base-ubuntu-22:2026.2.3`. Used only when `repo.sh package_container` cannot be run. |
| `playback.toml` | Non-interactive input for `./repo.sh template replay`. Pre-populates all answers `repo template new` would otherwise ask via `questionary`. |
| `README.md` | This file. |

## Build

The image is built **out-of-band** from the checkout at
`~/dev/git/omniverse/kit-app-template/` on the host (`<HOST_IP>`, reached via
SSH).

### One-time setup — bypass the EULA prompt

`./repo.sh template …` walks an interactive `questionary` prompt for the
NVIDIA Software License Agreement on first run. It reads from stdin via
`prompt_toolkit`, so an SSH-driven non-TTY caller hits `EOFError`.

The prompt is gated on a breadcrumb file:

```python
# repo_kit_template/.../template_tool.py
self.eula_breadcrumb = Path(
    tool_config.get("eula_path", resolve_tokens("${root}/.omniverse_eula_accepted.txt"))
)
```

Touch it once after reading and accepting the terms:

```bash
touch ~/dev/git/omniverse/kit-app-template/.omniverse_eula_accepted.txt
```

This is equivalent to selecting "Yes" at the prompt — the same `touch` that
the prompt would do on acceptance.

### Render the templates non-interactively

The `playback.toml` in this directory is a hand-written replacement for what
`./repo.sh template new --generate-playback FILE.toml` would produce
interactively. Render with:

```bash
cd ~/dev/git/omniverse/kit-app-template
./repo.sh template replay /path/to/OPCUA-OpenUSD/omniverse-kit/playback.toml
```

This produces:

```
source/apps/axel.robot_twin_viewer.kit            # main USD viewer app
source/apps/axel.robot_twin_viewer_streaming.kit  # streaming layer (omni.kit.livestream.app)
source/extensions/axel.robot_twin_viewer_setup/   # python setup extension (load stage, reload live.usda)
source/extensions/axel.robot_twin_viewer_messaging/
```

### Customize the rendered template

After rendering, two files are patched manually in-tree:

**`source/apps/axel.robot_twin_viewer.kit`** — auto-load the mounted stage:

```toml
[settings.app]
content.emptyStageOnStart = false        # was true
auto_load_usd = "/stage/stage.usda"      # added — read by setup extension
```

**`source/extensions/axel.robot_twin_viewer_setup/axel/robot_twin_viewer_setup/setup.py`** —
poll `live.usda` for bridge-driven updates and enable USD live mode:

```python
import os
from pxr import Sdf
# ... (existing imports) ...

class SetupExtension(omni.ext.IExt):
    def on_startup(self, _ext_id):
        # ... existing code ...
        self._live_poll_task = asyncio.ensure_future(self._poll_live_layer())

    async def _poll_live_layer(self):
        try:
            import omni.client
            try: omni.client.live_set_default_enabled(True)
            except Exception: pass
        except Exception: pass
        live_path = os.environ.get("ROBOT_TWIN_LIVE_LAYER", "/stage/live.usda")
        last_mtime = 0
        while True:
            try:
                mtime = os.path.getmtime(live_path)
                if mtime != last_mtime:
                    last_mtime = mtime
                    layer = Sdf.Layer.Find(live_path)
                    if layer:
                        layer.Reload(force=True)
            except FileNotFoundError:
                pass
            except Exception:
                pass
            await asyncio.sleep(0.25)
```

### Build the Kit app

```bash
cd ~/dev/git/omniverse/kit-app-template
./repo.sh build           # ~20-30 GB SDK download on first run; ~10-20 min
```

Build artifacts land in `_build/linux-x86_64/release/` along with launch
scripts `axel.robot_twin_viewer.sh` and `axel.robot_twin_viewer_streaming.sh`.

### Build the container image

```bash
cd ~/dev/git/omniverse/kit-app-template
./repo.sh package_container \
    --app axel.robot_twin_viewer_streaming \
    --image-tag axel/robot_twin_viewer:0.1.0
```

This is the preferred path: the upstream tool generates a Dockerfile based on
`nvcr.io/nvidia/omniverse/ov-base-ubuntu-22:2026.2.3`, splits the image into
a base layer (Kit kernel + extension cache) and an app layer (everything
else), and builds. The base image pulls without NGC auth.

If for some reason `repo.sh package_container` cannot be used, the local
`Dockerfile` in this directory replicates the same pattern as a fallback. Run
it from the kit-app-template repo root:

```bash
docker build \
    --build-arg KIT_BUILD_DIR=_build/linux-x86_64/release \
    --build-arg KIT_APP=axel.robot_twin_viewer_streaming \
    -t axel/robot_twin_viewer:0.1.0 \
    -f /path/to/OPCUA-OpenUSD/omniverse-kit/Dockerfile \
    .
```

## Run

Once the image exists locally:

```bash
cd ~/dev/git/my_experiments/OPCUA-OpenUSD
docker compose up -d omniverse-kit
docker compose logs -f omniverse-kit
```

Then open **`https://stack.local/usd/`** in a Mac browser on the same LAN.

To restart after a code change:

```bash
docker compose restart omniverse-kit
```

To rebuild end-to-end:

```bash
# On the host:
cd ~/dev/git/omniverse/kit-app-template
./repo.sh build
./repo.sh package_container --app axel.robot_twin_viewer_streaming --image-tag axel/robot_twin_viewer:0.1.0

# Then:
cd ~/dev/git/my_experiments/OPCUA-OpenUSD
docker compose up -d --force-recreate omniverse-kit
```

## GPU coordination with vLLM

vLLM (Nemotron Nano 8B) currently uses GPU 0 (~41 GB). The Kit container is
pinned to GPU 1 (~48 GB free). They are independent and can run concurrently
without coordination.

**Do not** restart vLLM with `--tensor-parallel-size 2` while the Kit
container is up — it will OOM.

## Verifying the stack

```bash
# Signaling layer reachable through Traefik?
curl -sk https://stack.local/usd/ -o /dev/null -w "%{http_code}\n"

# Container running on the right GPU?
docker exec rt-omniverse nvidia-smi -L

# Stage volume mounted?
docker exec rt-omniverse ls /stage/
```

## Acceptance gate

- [ ] `https://stack.local/usd/` returns non-404 HTML
- [ ] The browser displays the robot cell
- [ ] Joint rotations from OPC UA reflect within ~500 ms in the viewer
- [ ] The container runs only on GPU 1; vLLM keeps GPU 0

## Caveats

- **WebRTC ICE / NAT.** The signaling channel is proxied via Traefik but the
  media stream goes UDP-direct from the browser to the host on
  `47995-48005/udp`. On the LAN this works directly; remote clients would
  need a TURN server or equivalent.
- **HTTPS in WebRTC handshake.** Browsers require a secure context for
  `getUserMedia`/`RTCPeerConnection`. Traefik already terminates TLS for
  `stack.local`, so the page is served over HTTPS. If signaling URLs are
  rewritten, the Kit app's livestream extension must be told its public
  base URL (`/usd/`) — see `omni.kit.livestream.app` settings if a strip-prefix
  causes a 404 on the signaling websocket.
- **Firewall.** Open UDP `47995-48005` on the host firewall to the LAN.
- **GPU 1 is mandatory.** Anything else risks colliding with vLLM on GPU 0.
