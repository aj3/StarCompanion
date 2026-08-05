"""Reviewed SPDX choices for the frozen runtime graph."""

from __future__ import annotations


RUNTIME_LICENSES = {
    "annotated-types": "MIT",
    "cffi": "MIT-0",
    "cryptography": "Apache-2.0 OR BSD-3-Clause",
    "jinja2": "BSD-3-Clause",
    "markupsafe": "BSD-3-Clause",
    "pycparser": "BSD-3-Clause",
    "pydantic": "MIT",
    "pydantic-core": "MIT",
    "pyside6": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    "pyside6-addons": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    "pyside6-essentials": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    "shiboken6": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    "typing-extensions": "PSF-2.0",
    "typing-inspection": "MIT",
    "tzdata": "Apache-2.0",
    "zstandard": "BSD-3-Clause",
}


def cyclonedx_license(expression: str) -> list[dict[str, object]]:
    """Render one SPDX identifier or expression for CycloneDX."""
    if " OR " in expression or " AND " in expression:
        return [{"expression": expression}]
    return [{"license": {"id": expression}}]


__all__ = ["RUNTIME_LICENSES", "cyclonedx_license"]
