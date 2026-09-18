from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import LinearConstraint, least_squares, minimize

from .models import (
    AcousticCoordinate,
    AnalysisError,
    AnalysisSettings,
    AssetTuningResult,
    MappingPoint,
    RtpcControlPoint,
)

_LAMBDA_CANDIDATES = np.power(10.0, np.arange(-4.0, 5.0))


@dataclass(slots=True)
class MappingBuild:
    mapping_points: list[MappingPoint]
    asset_results: list[AssetTuningResult]
    rtpc_curves: dict[int, list[RtpcControlPoint]]
    smoothing_lambda: float
    max_linear_interpolation_error_cents: float
    warnings: list[str]


def _round_wwise_cents(value: float) -> int:
    """Round to Wwise's integer-cent precision, with halves away from zero."""
    return math.floor(value + 0.5) if value >= 0.0 else math.ceil(value - 0.5)


def _difference_matrices(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    first = np.zeros((len(x) - 1, len(x)), dtype=np.float64)
    for row in range(len(x) - 1):
        first[row, row] = -1.0
        first[row, row + 1] = 1.0
    curvature = np.zeros((max(0, len(x) - 2), len(x)), dtype=np.float64)
    for row in range(len(x) - 2):
        left_step = x[row + 1] - x[row]
        right_step = x[row + 2] - x[row + 1]
        curvature[row, row] = 1.0 / left_step
        curvature[row, row + 1] = -(1.0 / left_step + 1.0 / right_step)
        curvature[row, row + 2] = 1.0 / right_step
    return first, curvature


def _fit_once(
    y: np.ndarray,
    weights: np.ndarray,
    smoothing_lambda: float,
    first: np.ndarray,
    curvature: np.ndarray,
    constraint_lower: np.ndarray,
    initial: np.ndarray | None = None,
) -> np.ndarray:
    active_weights = np.maximum(weights, 0.0)
    hessian = np.diag(active_weights)
    if len(curvature):
        hessian = hessian + smoothing_lambda * (curvature.T @ curvature)
    hessian = hessian + np.eye(len(y)) * 1e-10
    right = active_weights * y

    def objective(values: np.ndarray) -> float:
        return float(values @ hessian @ values - 2.0 * right @ values)

    def gradient(values: np.ndarray) -> np.ndarray:
        return 2.0 * (hessian @ values - right)

    start = initial if initial is not None else np.maximum.accumulate(y)
    constraint = LinearConstraint(first, constraint_lower, np.full(len(first), np.inf))
    result = minimize(
        objective,
        start,
        jac=gradient,
        constraints=[constraint],
        method="SLSQP",
        options={"ftol": 1e-11, "maxiter": 1000},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        raise AnalysisError(f"Monotonic mapping fit failed: {result.message}")
    return np.asarray(result.x, dtype=np.float64)


def _select_smoothing_lambda(
    y: np.ndarray,
    weights: np.ndarray,
    first: np.ndarray,
    curvature: np.ndarray,
    constraint_lower: np.ndarray,
) -> float:
    if len(y) <= 3:
        return 1.0
    scores: list[float] = []
    for candidate in _LAMBDA_CANDIDATES:
        errors: list[float] = []
        for held_out in range(len(y)):
            fold_weights = weights.copy()
            fold_weights[held_out] = 0.0
            fitted = _fit_once(
                y, fold_weights, float(candidate), first, curvature, constraint_lower
            )
            errors.append(float(weights[held_out] * (fitted[held_out] - y[held_out]) ** 2))
        scores.append(float(np.sum(errors) / max(np.sum(weights), 1e-9)))
    best_index = int(np.argmin(scores))
    fold_errors: list[float] = []
    best_candidate = float(_LAMBDA_CANDIDATES[best_index])
    for held_out in range(len(y)):
        fold_weights = weights.copy()
        fold_weights[held_out] = 0.0
        fitted = _fit_once(y, fold_weights, best_candidate, first, curvature, constraint_lower)
        fold_errors.append(float((fitted[held_out] - y[held_out]) ** 2))
    tolerance = scores[best_index] + 2.0 * float(
        np.std(fold_errors, ddof=1) / math.sqrt(len(fold_errors))
    )
    eligible = [
        candidate
        for candidate, score in zip(_LAMBDA_CANDIDATES, scores, strict=True)
        if score <= tolerance
    ]
    return float(max(eligible))


def _fit_log_baseline(x_rpm: np.ndarray, y_cents: np.ndarray, weights: np.ndarray) -> np.ndarray:
    initial_a = 1200.0 / math.log(2.0)
    initial_k = 0.0
    initial_b = float(np.average(y_cents - initial_a * np.log(x_rpm), weights=weights))

    def residuals(parameters: np.ndarray) -> np.ndarray:
        a, k, b = parameters
        model = a * np.log(x_rpm + k) + b
        return np.sqrt(weights) * (model - y_cents)

    fit = least_squares(
        residuals,
        np.asarray([initial_a, initial_k, initial_b]),
        bounds=(
            np.asarray([1.0, -0.95 * float(x_rpm[0]), -1e6]),
            np.asarray([1e5, 20.0 * float(x_rpm[-1]), 1e6]),
        ),
        loss="soft_l1",
        f_scale=10.0,
        max_nfev=5000,
    )
    if not fit.success:
        raise AnalysisError(f"Mapping baseline fit failed: {fit.message}")
    a, k, b = fit.x
    return a * np.log(x_rpm + k) + b


def fit_master_mapping(
    coordinates: list[AcousticCoordinate], settings: AnalysisSettings
) -> MappingBuild:
    if len(coordinates) < 3:
        raise AnalysisError("At least three acoustic coordinates are required for mapping.")
    x_rpm = np.asarray([coordinate.asset.rpm for coordinate in coordinates], dtype=np.float64)
    if np.any(np.diff(x_rpm) <= 0):
        raise AnalysisError("RPM coordinates must be strictly increasing.")
    x = (x_rpm - x_rpm[0]) / (x_rpm[-1] - x_rpm[0])
    y_scale = 1200.0
    measured_cents = np.asarray(
        [coordinate.measured_pitch_cents for coordinate in coordinates], dtype=np.float64
    )
    base_weights = np.clip(
        np.asarray([coordinate.confidence for coordinate in coordinates], dtype=np.float64),
        0.05,
        1.0,
    )
    first, curvature = _difference_matrices(x)
    baseline_cents = _fit_log_baseline(x_rpm, measured_cents, base_weights)
    baseline = baseline_cents / y_scale
    y = (measured_cents - baseline_cents) / y_scale
    constraint_lower = -(first @ baseline)
    smoothing_lambda = _select_smoothing_lambda(
        y, base_weights, first, curvature, constraint_lower
    )

    robust_weights = base_weights.copy()
    fitted = _fit_once(
        y, robust_weights, smoothing_lambda, first, curvature, constraint_lower
    )
    for _ in range(10):
        residuals_cents = (y - fitted) * y_scale
        scale = max(3.0, 1.4826 * float(np.median(np.abs(residuals_cents))))
        ratio = np.abs(residuals_cents) / (4.685 * scale)
        biweight = np.square(1.0 - np.square(ratio))
        biweight[ratio >= 1.0] = 1e-4
        next_weights = base_weights * biweight
        updated = _fit_once(
            y,
            next_weights,
            smoothing_lambda,
            first,
            curvature,
            constraint_lower,
            fitted,
        )
        if np.max(np.abs(updated - fitted)) < 1e-9:
            fitted = updated
            robust_weights = next_weights
            break
        fitted = updated
        robust_weights = next_weights

    fitted_cents = baseline_cents + fitted * y_scale
    interpolator = PchipInterpolator(x_rpm, fitted_cents, extrapolate=False)
    first_plot_rpm = math.ceil(x_rpm[0] / 100.0) * 100
    plot_rpm = {float(rpm) for rpm in x_rpm}
    plot_rpm.update(
        float(rpm)
        for rpm in range(int(first_plot_rpm), int(math.floor(x_rpm[-1])) + 1, 100)
    )
    mapping_points = [
        MappingPoint(rpm, float(interpolator(rpm))) for rpm in sorted(plot_rpm)
    ]

    raw_residuals = measured_cents - fitted_cents
    residual_scale = max(3.0, 1.4826 * float(np.median(np.abs(raw_residuals))))
    asset_results: list[AssetTuningResult] = []
    for index, coordinate in enumerate(coordinates):
        raw_asset_offset = float(fitted_cents[index] - coordinate.measured_pitch_cents)
        asset_offset = _round_wwise_cents(raw_asset_offset)
        reasons: list[str] = []
        if coordinate.status != "OK":
            reasons.extend(coordinate.status.removeprefix("WARNING:").split("|"))
        if robust_weights[index] < base_weights[index] * 0.1 or abs(raw_residuals[index]) > max(
            15.0, 3.0 * residual_scale
        ):
            reasons.append("MODEL_OUTLIER")
        if abs(asset_offset) > settings.wwise_pitch_limit_cents:
            reasons.append("WWISE_LIMIT")
        reasons = list(dict.fromkeys(reason for reason in reasons if reason))
        asset_results.append(
            AssetTuningResult(
                asset=coordinate.asset,
                measured_pitch_cents=coordinate.measured_pitch_cents,
                model_pitch_cents=float(fitted_cents[index]),
                raw_asset_offset_cents=raw_asset_offset,
                asset_offset_cents=asset_offset,
                confidence=coordinate.confidence,
                status="WARNING:" + "|".join(reasons) if reasons else "OK",
            )
        )

    curves: dict[int, list[RtpcControlPoint]] = {}
    max_interpolation_error = 0.0
    for index, result in enumerate(asset_results):
        lower = x_rpm[index - 1] if index > 0 else x_rpm[index]
        upper = x_rpm[index + 1] if index < len(x_rpm) - 1 else x_rpm[index]
        first_grid = math.ceil(lower / settings.rtpc_step_rpm) * settings.rtpc_step_rpm
        rpm_points = {float(lower), float(upper), float(result.asset.rpm)}
        rpm_points.update(
            float(rpm)
            for rpm in range(int(first_grid), int(math.floor(upper)) + 1, settings.rtpc_step_rpm)
        )
        sorted_rpm = sorted(rpm_points)
        native_model_pitch = float(interpolator(result.asset.rpm))
        control_points: list[RtpcControlPoint] = []
        for rpm in sorted_rpm:
            rtpc_pitch = float(interpolator(rpm) - native_model_pitch)
            final_pitch = result.asset_offset_cents + rtpc_pitch
            reasons: list[str] = []
            if abs(rtpc_pitch) > settings.wwise_pitch_limit_cents or abs(
                final_pitch
            ) > settings.wwise_pitch_limit_cents:
                reasons.append("WWISE_LIMIT")
            control_points.append(
                RtpcControlPoint(
                    asset=result.asset,
                    rtpc_rpm=rpm,
                    rtpc_pitch_cents=rtpc_pitch,
                    asset_offset_cents=result.asset_offset_cents,
                    final_pitch_cents=final_pitch,
                    status="WARNING:" + "|".join(reasons) if reasons else "OK",
                )
            )
        curves[result.asset.rpm] = control_points

        for left, right in zip(control_points, control_points[1:], strict=False):
            samples = np.linspace(left.rtpc_rpm, right.rtpc_rpm, 21)
            exact = interpolator(samples) - native_model_pitch
            linear = np.interp(
                samples,
                [left.rtpc_rpm, right.rtpc_rpm],
                [left.rtpc_pitch_cents, right.rtpc_pitch_cents],
            )
            max_interpolation_error = max(
                max_interpolation_error, float(np.max(np.abs(exact - linear)))
            )

    warnings: list[str] = []
    if max_interpolation_error > 2.0:
        warnings.append(
            "500 RPM linear RTPC control points deviate from the fitted PCHIP mapping by "
            f"up to {max_interpolation_error:.2f} cents."
        )
    return MappingBuild(
        mapping_points=mapping_points,
        asset_results=asset_results,
        rtpc_curves=curves,
        smoothing_lambda=smoothing_lambda,
        max_linear_interpolation_error_cents=max_interpolation_error,
        warnings=warnings,
    )
