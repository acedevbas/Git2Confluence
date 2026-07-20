"""Helpers for detecting repository changes that affect an OpenAPI source."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


DEFAULT_OPENAPI_PATH_MARKERS = ("swagger", "openapi")


def _normalize_repo_path(path: str) -> str:
    """Normalize a configured or Git-reported repository path for comparison."""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def touches_openapi_source(
    changed_files: Iterable[str],
    source_path: str,
    path_markers: Sequence[str] = DEFAULT_OPENAPI_PATH_MARKERS,
) -> bool:
    """Return whether changed files can affect the configured OpenAPI source.

    Every file below ``source_path`` belongs to the source because split specs
    may keep operations and shared components in files whose names contain
    neither ``swagger`` nor ``openapi``. The marker fallback preserves the
    legacy behavior for projects with incomplete or historical configuration.
    """
    normalized_source = _normalize_repo_path(source_path)
    source_prefix = f"{normalized_source}/" if normalized_source else ""
    normalized_markers = tuple(marker.lower() for marker in path_markers)

    for file_path in changed_files:
        normalized_file = _normalize_repo_path(file_path)
        if normalized_source and (
            normalized_file == normalized_source
            or normalized_file.startswith(source_prefix)
        ):
            return True

        lowered_file = normalized_file.lower()
        if any(marker in lowered_file for marker in normalized_markers):
            return True

    return False
