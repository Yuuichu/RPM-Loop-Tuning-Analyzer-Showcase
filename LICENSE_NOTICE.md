# Licence and third-party notice

## RPM Loop Tuning Analyzer

Released under the **MIT licence** (`Copyright (c) 2026 Yuuichu`). The full licence text is included in this showcase as `LICENSE`, and the same file lives in the development repository.

## Third-party software

The tool is built on:

| Component | Role | Licence |
|---|---|---|
| `numpy` | Array maths | BSD-3-Clause |
| `scipy` | `PchipInterpolator`, `hann` window, Gaussian filter, optimisation | BSD-3-Clause |
| `soundfile` | WAV/FLAC reading | BSD-3-Clause |
| `matplotlib` | Chart export | PSF-based (matplotlib licence) |
| `PySide6` | Desktop GUI (Qt for Python) | LGPL-3.0 |
| `librosa` | Declared dependency | ISC |

None of these are vendored into this repository; they are installed from PyPI per `pyproject.toml`. If this code is ever distributed in binary form, PySide6's LGPL terms are the one dependency worth reviewing carefully.

## Wwise

**Wwise is a commercial product and a trademark of Audiokinetic Inc.** This tool does not include, link against or redistribute any Wwise code or content. It produces numbers — Asset Offsets and Voice Pitch RTPC control points — that a human applies by hand inside a licensed Wwise installation.

## Audio content

**No audio is included** in this showcase.

The example tables under `examples/` were produced from a **synthetic sample fixture** created for testing, not from any production or client recording. No production audio, project or asset names are included.