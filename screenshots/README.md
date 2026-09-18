# Screenshots

The two charts in `demo/` are real tool output and already carry most of the visual story. What is missing is the application itself.

## Already included

| File | Shows |
|---|---|
| `demo/master_curve.png` | Measured acoustic pitch per loop vs. the fitted monotone master mapping |
| `demo/per_asset_rtpc.png` | Per-asset Voice Pitch RTPC curves, each crossing zero at its own native RPM |

## What should be captured next

1. **`hero-gui.png`** — the PySide6 application after a run: the results table with confidence and status per asset, plus the curve view. This is the evidence that it is a usable tool rather than a script.
2. **`diagnostics.png`** — a run containing a deliberately bad file, showing `LOW_CONFIDENCE` / `OUTLIER` statuses. The diagnostic path is a feature and should be visible.
3. **`wwise-rtpc.png`** — the exported control points applied to a Voice Pitch RTPC curve in Wwise. This closes the loop from measurement to the thing an audio designer actually uses.

## Constraints on what may be shown

- **Use synthetic or self-recorded fixture audio only.** No client or production vehicle recordings, and no production project names in any filename, table or window title.
- The current demo artifacts come from a synthetic sample fixture; any replacement capture must keep the same property.
- No absolute local paths in the UI or terminal.

## Note on the existing charts

The bundled charts are genuine tool output from the sample fixture, which is why they are usable as-is. If the fixture's naming is ever considered sensitive, re-run the tool on anonymised fixture data and replace these files — the numbers, not the labels, are the point.