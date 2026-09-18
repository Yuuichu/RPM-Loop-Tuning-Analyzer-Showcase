# Method

> **English** | [简体中文](method.zh-CN.md)

The tool answers one question: **given a set of steady loops labelled with nominal RPMs, what is the true relationship between RPM and acoustic pitch, and what does each individual loop need in order to sit on it?**

It is solved in four stages.

## Stage 1 — Pitch-invariant representation

Two recordings of the same engine at different RPMs differ in pitch *and* in spectral shape. To compare them, pitch must become a translation.

1. **Trim.** The first and last 5% of each loop are discarded (a configurable fraction, default `0.05`), removing fades and loop-boundary artefacts.
2. **Windowed magnitude spectrum.** Frames are taken with a Hann window (`scipy.signal.windows.hann`) and transformed with an `rfft`. Multiple windows are extracted per asset and reduced later.
3. **Log compression.** Magnitudes are compressed with `log1p`, which stops a few very loud harmonics from dominating the comparison.
4. **Cents-grid resampling.** The spectrum is interpolated onto a logarithmic grid:

   ```text
   cents(f)  = 1200 · log2(f / f_min)
   f(cents)  = f_min · 2^(cents / 1200)
   ```

   On this grid, transposing a sound by *n* cents is exactly a shift of *n* bins.

5. **Spectral whitening** (default on). A Gaussian-smoothed envelope of the log spectrum is subtracted, leaving local harmonic structure. Without this step, the correlation would largely measure *how loud each harmonic is* rather than *where it is*; with it, the correlation peak reflects interval relationships.
6. **Multi-window median.** The per-window profiles are combined with a **per-bin median** across windows, so a single noisy window cannot define the asset.

## Stage 2 — Pairwise interval measurement

For each pair of loops, the relative pitch interval is found by correlating the cents-grid profiles.

Because the two loops have different nominal RPMs, the expected interval is known approximately:

```text
expected_interval_cents(a, b) = 1200 · log2(rpm_b / rpm_a)
```

The search is then two-stage:

| Stage | Search range | Purpose |
|---|---|---|
| **Coarse** | full ±1200 cents (configurable `coarse_search_margin_cents`) | find the true interval for **adjacent** RPM pairs |
| **Refine** | ±150 cents (`refine_search_radius_cents`) around the predicted value | precise measurement for **non-adjacent** pairs |

Each measurement carries quality evidence: peak correlation, peak prominence, window-to-window consistency, and whether the peak landed on a search boundary (a boundary hit means "the search was too narrow", which is treated as a warning rather than a result).

## Stage 3 — Anchored robust global solve

Pairwise intervals are *relative*: measuring `A → B → C` accumulates error, and a single bad recording can drag the whole chain. Instead, all assets are solved simultaneously.

Each measurement gives a difference equation:

```text
A_j − A_i ≈ D_ij
```

which becomes one row of a linear system, weighted by measurement quality. The system has a gauge freedom (adding a constant to every `A` changes nothing), so it is solved with an **equality constraint** fixing one anchor at 0 cents rather than by arbitrarily pinning the first file.

The solve is **iteratively reweighted least squares with Tukey's biweight**:

```text
scale  = max(2.5, 1.4826 · median(|residuals|))     # MAD-based
cutoff = 4.685 · scale
w      = (1 − (r/cutoff)²)²                          # floored at 1e-4
```

up to 12 iterations, converging when the weights stop changing. A mis-measured or noisy recording is progressively down-weighted instead of pulling the global solution off target.

**Anchor selection** is deliberate: among assets whose preliminary confidence clears the low-confidence threshold, the anchor is the one **closest to the median RPM**, tie-broken by higher confidence. A central, well-measured loop makes a far better reference than an arbitrary one.

Every asset then receives a confidence and a diagnosis:

```text
pair_quality = 0.65 · correlation_component
             + 0.25 · peak_prominence
             + 0.10 · window_consistency
fit          = exp(−|solver_residual| / 12)
confidence   = 0.75 · quality(evidence, fit) + 0.25 · graph_coverage
```

Diagnostics: `LOW_CONFIDENCE`, `LOW_CONNECTIVITY` (too few pairs for an asset), `POOR_GLOBAL_FIT` (median residual above tolerance) and `NON_MONOTONIC_MEASUREMENT` (the solve places a higher-RPM asset below a lower-RPM one, which is physically implausible for a steady engine loop). Pairs whose weight collapsed are marked `OUTLIER`.

Note also that the pair graph must be **connected**: if some asset has no valid measurement path to the rest, the solve refuses to run rather than silently producing an unsupported number.

## Stage 4 — Monotone master mapping

The solved acoustic pitches are then regressed against RPM:

```text
model(R) = a · log(R + K) + b  +  residual(R)
```

- The `log(R + K)` term is the physical baseline: pitch is roughly proportional to the logarithm of RPM.
- The residual term is **non-parametric and monotonicity-constrained**, fitted with an analytic-gradient optimiser and a smoothing weight chosen by selection rather than by hand.
- The final curve is evaluated through **PCHIP** interpolation (`scipy.interpolate.PchipInterpolator`), which is monotone-preserving by construction, with extrapolation disabled.

The result is a curve that cannot fold back on itself, which is what makes it safe to convert into RTPC control points.

## Stage 5 — Export

Two UTF-8 (BOM) tables and two charts:

- **Asset offsets**: measured pitch, model pitch, raw offset, integer offset, confidence, status.
- **RTPC control points**: per asset, RPM → RTPC pitch, with the combined final pitch.
- **Charts**: the master mapping against measured points, and the per-asset RTPC curves.

See `docs/rtpc-math.md` for the exact formulas that produce those numbers.