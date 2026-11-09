"""Load and validate the application feature-status catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FUNCTIONALITY_LEVELS = (
    "Fully functional",
    "Partially functional",
    "Read-only",
    "Not implemented",
)


def load_feature_catalog(path: Path) -> list[dict[str, str]]:
    try:
        catalog: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not load feature catalog {path}: {error}") from error

    if not isinstance(catalog, dict) or not isinstance(catalog.get("features"), list):
        raise ValueError(f"Feature catalog {path} must contain a features list")

    features: list[dict[str, str]] = []
    feature_ids: set[str] = set()
    for index, feature in enumerate(catalog["features"], start=1):
        if not isinstance(feature, dict):
            raise ValueError(f"Feature {index} in {path} must be an object")
        for field in ("id", "name", "area", "level", "details"):
            value = feature.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Feature {index} in {path} must have a non-empty {field}")
        if feature["id"] in feature_ids:
            raise ValueError(f"Feature catalog {path} has duplicate id {feature['id']!r}")
        if feature["level"] not in FUNCTIONALITY_LEVELS:
            raise ValueError(
                f"Feature {feature['id']!r} in {path} has unknown level "
                f"{feature['level']!r}"
            )
        feature_ids.add(feature["id"])
        features.append(feature)

    return features
