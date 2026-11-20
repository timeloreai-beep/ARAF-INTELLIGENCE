from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfluenceResult:
    allowed: bool
    confidence: float
    reasons: tuple[str, ...]


def evaluate_five_layers(
    *,
    macro_green: bool,
    smc_green: bool,
    mtf_green: bool,
    volume_green: bool,
    risk_green: bool,
) -> ConfluenceResult:
    layers = {
        "macro": macro_green,
        "smc": smc_green,
        "mtf": mtf_green,
        "volume": volume_green,
        "risk": risk_green,
    }
    failed = tuple(name for name, green in layers.items() if not green)
    confidence = sum(layers.values()) / len(layers) * 100
    return ConfluenceResult(not failed, confidence, tuple(f"{name}_failed" for name in failed))
