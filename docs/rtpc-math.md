# RTPC mathematics and output semantics

This document defines exactly what the exported numbers mean, so they can be checked by hand against the CSVs in `examples/`.

## Notation

| Symbol | Meaning |
|---|---|
| `R` | RPM |
| `NativeRPM` | The nominal RPM label of a given loop |
| `MeasuredPitch` | The loop's solved acoustic pitch, in cents (relative to the anchor) |
| `ModelPitch(R)` | The fitted master mapping evaluated at `R`, in cents |
| `AssetOffset` | The integer-cent offset applied to a specific loop in Wwise |

## 1. The two outputs, and why both exist

The final pitch of an asset at any RPM is the sum of a per-asset constant and a per-asset curve:

```text
FinalPitch(R) = AssetOffset + RTPCPitch(R)
```

- **`AssetOffset`** is a single integer number of cents. It corrects the *constant* disagreement between the loop's own pitch and the master mapping at that loop's native RPM.
- **`RTPCPitch(R)`** is the curve that moves the loop along with RPM. It is the replacement for a hand-authored Smart Pitch Curve.

Splitting the correction this way means the curve is **portable across assets**: every loop can share the same *kind* of curve, and only the constant differs.

## 2. Computing the asset offset

```text
RawAssetOffset = ModelPitch(NativeRPM) − MeasuredPitch
AssetOffset    = round_to_integer(RawAssetOffset)     # Wwise integer cents
```

The rounding is a genuine quantisation: Wwise Asset Offsets are integers. The **unrounded** value is kept in the export as `RawAssetOffsetCents`, so the residual is visible rather than hidden inside the rounding. That column is the diagnostic — an asset whose raw offset is 0.13 cents is well-aligned; one at 12.4 cents is not.

## 3. Computing the RTPC curve

```text
RTPCPitch(R) = ModelPitch(R) − ModelPitch(NativeRPM)
```

Two properties follow directly, and both matter:

1. **The curve is exactly 0 at the asset's own native RPM.** `RTPCPitch(NativeRPM) = 0` by construction, which is why the per-asset chart crosses zero at that asset's own RPM.
2. **The mapping is additive.** Because the curve is expressed relative to its own native RPM, it composes with `AssetOffset` without a second correction term.

Positive cents raise pitch, negative cents lower it.

## 4. Master mapping

```text
ModelPitch(R) = a · log(R + K) + b + residual(R)
```

- `log(R + K)` — the physical baseline. `K` exists so the expression remains defined and well-conditioned near zero RPM.
- `residual(R)` — a monotone non-parametric correction, so the model can follow a real engine's non-ideal behaviour while remaining strictly non-decreasing.
- Evaluation by **PCHIP**, chosen because it is monotone-preserving; a cubic spline through the same points can overshoot and introduce a local decrease, which would produce a pitch curve that reverses at some RPM.
- Extrapolation is **disabled**: outside the measured RPM range the model does not guess.

## 5. Cents, and the Wwise limit

The unusable-range limit is **±2400 cents** (`wwise_pitch_limit_cents`). Results outside it are flagged in the output; they are **never silently clamped**. A silently clamped value would look like a successful analysis while hiding the fact that the measurement or the input is wrong.

## 6. What the CSVs contain

`examples/example_asset_offsets.csv`:

| Column | Meaning |
|---|---|
| `Asset` | Loop filename |
| `NativeRPM` | Nominal RPM label |
| `MeasuredPitch` | Solved acoustic pitch (cents, anchored) |
| `ModelPitch` | Master mapping at `NativeRPM` |
| `RawAssetOffsetCents` | Unrounded `ModelPitch − MeasuredPitch` (diagnostic) |
| `AssetOffsetCents` | The integer to apply in Wwise |
| `Confidence` | 0–1 measurement confidence |
| `Status` | `OK` or a diagnostic such as `WARNING:LOW_CONFIDENCE`, `...|OUTLIER` |

`examples/example_rtpc_control_points.csv`:

| Column | Meaning |
|---|---|
| `Asset` | Loop filename |
| `NativeRPM` | Nominal RPM label (the zero point of this asset's curve) |
| `rtpc_RPM` | RTPC x-value: the RPM axis |
| `RTPCPitchCents` | RTPC y-value at that RPM |
| `AssetOffsetCents` | This asset's constant offset |
| `FinalPitchCents` | `AssetOffset + RTPCPitch` — the resulting pitch |
| `Status` | Point status |

## 7. What the numbers do not mean

- **They are relative, not absolute.** The whole system is anchored to one asset, so "pitch" means "pitch relative to the anchor". Setting the absolute pitch of the vehicle remains an artistic decision.
- **They assume the RPM labels are roughly right.** The two-stage search tolerates label error of about ±1200 cents of implied interval, but a badly mislabelled file defeats it.
- **They assume steady loops.** A pitch sweep violates the premise that one loop has one pitch.