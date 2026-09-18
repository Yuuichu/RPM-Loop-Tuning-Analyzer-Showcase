from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal.windows import hann

from .audio import load_mono
from .models import AnalysisError, AnalysisSettings, AudioAsset, CancelToken


@dataclass(slots=True)
class SpectralFeature:
    cents_grid: np.ndarray
    profile: np.ndarray
    windows: np.ndarray


def _normalise(values: np.ndarray) -> np.ndarray:
    finite = np.isfinite(values)
    result = np.full_like(values, np.nan, dtype=np.float64)
    if np.count_nonzero(finite) < 16:
        return result
    selected = values[finite]
    selected = selected - np.median(selected)
    scale = 1.4826 * np.median(np.abs(selected))
    if scale < 1e-8:
        scale = np.std(selected)
    if scale < 1e-8:
        return result
    result[finite] = selected / scale
    return result


def _frame_positions(length: int, frame_length: int, count: int) -> np.ndarray:
    if length <= frame_length:
        return np.array([0], dtype=int)
    return np.linspace(0, length - frame_length, count, dtype=int)


def extract_feature(
    asset: AudioAsset,
    settings: AnalysisSettings,
    cancel_token: CancelToken | None = None,
) -> SpectralFeature:
    token = cancel_token or CancelToken()
    token.raise_if_cancelled()
    samples = load_mono(asset)
    trim = int(round(len(samples) * settings.trim_fraction))
    trimmed = samples[trim : len(samples) - trim] if trim else samples
    if len(trimmed) < 256:
        raise AnalysisError(f"Audio is too short for spectral analysis: {asset.filename}")

    max_hz = min(settings.max_frequency_hz, asset.sample_rate * 0.5 * 0.999)
    if max_hz <= settings.min_frequency_hz:
        raise AnalysisError(f"Sample rate is too low for the selected range: {asset.filename}")
    grid_max = 1200.0 * np.log2(settings.max_frequency_hz / settings.min_frequency_hz)
    cents_grid = np.arange(0.0, grid_max + settings.cents_per_bin * 0.5, settings.cents_per_bin)
    target_hz = settings.min_frequency_hz * np.power(2.0, cents_grid / 1200.0)

    frame_length = min(settings.n_fft, len(trimmed))
    frame_count = settings.window_count if settings.multi_window_average else 1
    positions = _frame_positions(len(trimmed), frame_length, frame_count)
    window = hann(frame_length, sym=False)
    fft_hz = np.fft.rfftfreq(settings.n_fft, 1.0 / asset.sample_rate)
    valid_fft = (fft_hz >= settings.min_frequency_hz) & (fft_hz <= max_hz)
    valid_target = target_hz <= max_hz
    if np.count_nonzero(valid_fft) < 16 or np.count_nonzero(valid_target) < 16:
        raise AnalysisError(f"Selected frequency range has insufficient FFT bins: {asset.filename}")

    window_features: list[np.ndarray] = []
    for start in positions:
        token.raise_if_cancelled()
        frame = trimmed[start : start + frame_length] * window
        magnitude = np.abs(np.fft.rfft(frame, n=settings.n_fft))
        log_magnitude = np.log1p(magnitude)
        interpolated = np.full_like(cents_grid, np.nan, dtype=np.float64)
        interpolated[valid_target] = np.interp(
            target_hz[valid_target], fft_hz[valid_fft], log_magnitude[valid_fft]
        )
        if settings.spectral_whitening:
            sigma_bins = max(1.0, 100.0 / settings.cents_per_bin)
            envelope = gaussian_filter1d(interpolated[valid_target], sigma=sigma_bins, mode="nearest")
            interpolated[valid_target] -= envelope
        window_features.append(_normalise(interpolated))

    windows = np.stack(window_features)
    profile = np.full(windows.shape[1], np.nan, dtype=np.float64)
    usable_columns = np.any(np.isfinite(windows), axis=0)
    profile[usable_columns] = np.nanmedian(windows[:, usable_columns], axis=0)
    profile = _normalise(profile)
    return SpectralFeature(cents_grid=cents_grid, profile=profile, windows=windows)
