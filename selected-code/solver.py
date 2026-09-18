from __future__ import annotations

import math

from collections import deque

import numpy as np

from .models import AcousticCoordinate, AnalysisError, AnalysisSettings, AudioAsset, PairMeasurement


def _validate_connected(node_count: int, pairs: list[PairMeasurement]) -> None:
    adjacency: list[list[int]] = [[] for _ in range(node_count)]
    for pair in pairs:
        adjacency[pair.first_index].append(pair.second_index)
        adjacency[pair.second_index].append(pair.first_index)
    visited = {0}
    queue = deque([0])
    while queue:
        node = queue.popleft()
        for neighbour in adjacency[node]:
            if neighbour not in visited:
                visited.add(neighbour)
                queue.append(neighbour)
    if len(visited) != node_count:
        missing = ", ".join(str(index) for index in sorted(set(range(node_count)) - visited))
        raise AnalysisError(f"Pair graph is disconnected; unreachable asset indices: {missing}.")


def _design_matrix(node_count: int, pairs: list[PairMeasurement]) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.zeros((len(pairs), node_count), dtype=np.float64)
    observed = np.empty(len(pairs), dtype=np.float64)
    for row, pair in enumerate(pairs):
        matrix[row, pair.first_index] = -1.0
        matrix[row, pair.second_index] = 1.0
        observed[row] = pair.measured_interval_cents
    return matrix, observed


def _constrained_solve(
    matrix: np.ndarray, observed: np.ndarray, weights: np.ndarray, anchor: np.ndarray
) -> np.ndarray:
    normal = matrix.T @ (weights[:, None] * matrix)
    right = matrix.T @ (weights * observed)
    kkt = np.block(
        [[normal, anchor[:, None]], [anchor[None, :], np.zeros((1, 1), dtype=np.float64)]]
    )
    target = np.concatenate([right, np.array([0.0])])
    try:
        solution = np.linalg.solve(kkt, target)
    except np.linalg.LinAlgError as exc:
        raise AnalysisError("Global pitch solve is singular.") from exc
    return solution[:-1]


def _measurement_quality(pair: PairMeasurement) -> float:
    corr = np.clip((pair.correlation - 0.10) / 0.70, 0.0, 1.0)
    prominence = np.clip(pair.peak_prominence / 0.12, 0.0, 1.0)
    return float(0.65 * corr + 0.25 * prominence + 0.10 * pair.window_consistency)


def solve_acoustic_coordinates(
    assets: list[AudioAsset], pairs: list[PairMeasurement], settings: AnalysisSettings
) -> tuple[list[AcousticCoordinate], int]:
    node_count = len(assets)
    if node_count < 3 or len(pairs) < node_count - 1:
        raise AnalysisError("There are not enough valid pair measurements for a global solve.")
    _validate_connected(node_count, pairs)
    matrix, observed = _design_matrix(node_count, pairs)
    base_weights = np.asarray([pair.weight for pair in pairs], dtype=np.float64)
    robust_weights = base_weights.copy()
    incident: list[list[int]] = [[] for _ in range(node_count)]
    for pair_index, pair in enumerate(pairs):
        incident[pair.first_index].append(pair_index)
        incident[pair.second_index].append(pair_index)
    preliminary_confidence = np.asarray(
        [
            np.mean([_measurement_quality(pairs[index]) for index in pair_indices])
            if pair_indices
            else 0.0
            for pair_indices in incident
        ],
        dtype=np.float64,
    )
    median_rpm = float(np.median([asset.rpm for asset in assets]))
    eligible = [
        index
        for index in range(node_count)
        if preliminary_confidence[index] >= settings.low_confidence_threshold
    ]
    if eligible:
        anchor_index = min(
            eligible,
            key=lambda index: (
                abs(assets[index].rpm - median_rpm),
                -preliminary_confidence[index],
            ),
        )
    else:
        anchor_index = int(np.argmax(preliminary_confidence))
    anchor = np.zeros(node_count, dtype=np.float64)
    anchor[anchor_index] = 1.0

    offsets = np.zeros(node_count, dtype=np.float64)
    for _ in range(12):
        updated = _constrained_solve(matrix, observed, robust_weights, anchor)
        residuals = matrix @ updated - observed
        scale = max(2.5, 1.4826 * float(np.median(np.abs(residuals))))
        cutoff = 4.685 * scale
        ratio = np.abs(residuals) / cutoff
        biweight = np.square(1.0 - np.square(ratio))
        biweight[ratio >= 1.0] = 1e-4
        next_weights = base_weights * biweight
        offsets = updated
        if np.max(np.abs(next_weights - robust_weights)) < 1e-5:
            robust_weights = next_weights
            break
        robust_weights = next_weights

    solver_residuals = matrix @ offsets - observed
    for index, pair in enumerate(pairs):
        pair.solver_residual_cents = float(solver_residuals[index])
        if robust_weights[index] < base_weights[index] * 0.1:
            pair.status = "OUTLIER" if pair.status == "OK" else f"{pair.status}|OUTLIER"
    confidences = np.zeros(node_count, dtype=np.float64)
    expected_degree = min(node_count - 1, settings.pair_distance * 2)
    for node, pair_indices in enumerate(incident):
        qualities: list[float] = []
        for pair_index in pair_indices:
            pair = pairs[pair_index]
            measurement = _measurement_quality(pair)
            fit = math.exp(-abs(pair.solver_residual_cents) / 12.0)
            qualities.append(float(measurement * fit))
        pair_quality = float(np.mean(qualities)) if qualities else 0.0
        graph_coverage = min(1.0, len(pair_indices) / max(1, expected_degree))
        confidences[node] = np.clip(0.75 * pair_quality + 0.25 * graph_coverage, 0.0, 1.0)

    non_monotonic = np.zeros(node_count, dtype=bool)
    for index in range(node_count - 1):
        if offsets[index + 1] <= offsets[index]:
            non_monotonic[index] = True
            non_monotonic[index + 1] = True

    coordinates: list[AcousticCoordinate] = []
    minimum_degree = min(2, node_count - 1)
    for index, asset in enumerate(assets):
        reasons: list[str] = []
        if confidences[index] < settings.low_confidence_threshold:
            reasons.append("LOW_CONFIDENCE")
        if len(incident[index]) < minimum_degree:
            reasons.append("LOW_CONNECTIVITY")
        if incident[index] and np.median(
            [abs(pairs[pair_index].solver_residual_cents) for pair_index in incident[index]]
        ) > 15.0:
            reasons.append("POOR_GLOBAL_FIT")
        if non_monotonic[index]:
            reasons.append("NON_MONOTONIC_MEASUREMENT")
        status = "WARNING:" + "|".join(reasons) if reasons else "OK"
        coordinates.append(
            AcousticCoordinate(
                asset=asset,
                measured_pitch_cents=float(offsets[index]),
                confidence=float(confidences[index]),
                is_anchor=index == anchor_index,
                status=status,
            )
        )
    return coordinates, anchor_index
