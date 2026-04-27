# web-viewer-sample patches

Patches we apply on top of NVIDIA's
[`web-viewer-sample`](https://github.com/NVIDIA-Omniverse/web-viewer-sample)
checkout (`~/dev/git/omniverse/web-viewer-sample/`) before `npm run build`.

## Patches

### `Window.tsx.patch` — bypass the broken loadingStateQuery poll

`@nvidia/omniverse-webrtc-streaming-library@5.17.0` has a bug in its custom-
message dispatch path: `addCallback` evaluates `'event_type' in <msg>` after
the message has already been JSON-stringified, so the operand is a string,
not an object, and the `in` operator throws `TypeError`.

Result: `_pollForKitReady` keeps firing every 3 s, every call throws, and the
React UI's "Waiting for stream to begin" overlay never clears — even though
the WebRTC `MediaStream` is fully attached to `<video id="remote-video">`
and rendering 1920×1080 @ 60 fps.

The patch flips `showStream: true` (and the related state flags) inside
`_onStreamStarted` directly, before the broken poll fires, so the overlay
disappears immediately when the stream starts. The poll is still kicked
off afterwards — if NVIDIA later ships a fixed library, the `loadingState`
response will finally arrive and the existing handler will pick it up
without further changes here.

## Apply

```bash
cd ~/dev/git/omniverse/web-viewer-sample
patch -p1 < /path/to/OPCUA-OpenUSD/omniverse-kit/web-viewer-patches/Window.tsx.patch
# or just sed in-place; see omniverse-kit/README.md
npm run build
```

The build output is then served by the `stream-viewer` compose service via
the `VIEWER_DIST` env var (default
`/home/<user>/dev/git/omniverse/web-viewer-sample/dist`).
