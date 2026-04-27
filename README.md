# my_experiments

A public GitHub repo for my personal experiments — for learning and testing.

## Projects

### [mold-surrogate](mold-surrogate/) — Real-time injection molding fill prediction

A neural surrogate for plastic injection molding fill-time prediction.
Hele-Shaw eikonal physics solver paired with a U-Net surrogate that
predicts fill time and air-trap risk in milliseconds. For my personal
research only — not a Moldflow replacement.

- See [mold-surrogate/README.md](mold-surrogate/README.md) for details

### [OPCUA-OpenUSD](OPCUA-OpenUSD/) — Industry 4.0 digital-twin PoC

A containerized end-to-end demo wiring **OPC UA** (asyncua server simulating
a 6-axis robot) to **OpenUSD** (live USD overrides streamed via NVIDIA
Omniverse Kit + WebRTC), driven by a **Microsoft Agent Framework** advisory
agent that reasons over the OPC UA spec corpus (RAG over
[OPCFoundation/UA-for-AI-Prototype](https://github.com/OPCFoundation/UA-for-AI-Prototype),
generation by a self-hosted vLLM Nemotron). For my personal learning only —
not a production digital-twin platform, not affiliated with NVIDIA, OPC
Foundation, or Microsoft.

- See [OPCUA-OpenUSD/README.md](OPCUA-OpenUSD/README.md) for details
