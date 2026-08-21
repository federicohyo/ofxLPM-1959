# ofxLPM-1959

## Open issues

### Remote chip mode — membrane display not in real time

When running with `"remoteChip"` in `settings.json` and `chip_server.py` on the
Pi, the membrane scope strip and spiral are not in real time — the trace takes
many minutes instead of ~2 seconds to scroll across the panel.

The membrane data flows: Pixhawk → USB serial → `chip_server.py` → TCP →
`NetworkBridge` → `ofApp::update()` → `feed()`. The `chip_server.py` paces at
~1000 lines/s, and the app rate-limits to ~16 samples/frame. But the display
still shows compressed time.

Likely cause: `sigFrame` advances correctly but the spiral/scope timebase is
wrong, or the network buffering at the TCP level batches samples despite the
server pacing. Fix not yet found.
