"""Validate the machine-readable report emitted by the Windows signing step."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
SCHEMA = "starcompanion.authenticode.v2"
ARTIFACT_NAMES = ("StarCompanion.exe", "starcompanion-cli.exe")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_THUMBPRINT = re.compile(r"[0-9A-F]{40}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(report_path: Path, artifacts: dict[str, Path]) -> dict[str, object]:
    if set(artifacts) != set(ARTIFACT_NAMES):
        raise ValueError("the Authenticode report must cover both release executables")
    document = json.loads(
        report_path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(document, dict) or set(document) != {
        "schema",
        "status",
        "provider",
        "certificate",
        "timestamp_authorities",
        "artifacts",
    }:
        raise ValueError("unexpected Authenticode report shape")
    if document["schema"] != SCHEMA or document["status"] != "valid":
        raise ValueError("the Authenticode report does not declare a valid signature")

    if document["provider"] != "SignPath.io":
        raise ValueError("unexpected signing provider")

    certificate = document["certificate"]
    if not isinstance(certificate, dict) or set(certificate) != {
        "subject",
        "thumbprint",
        "not_after_utc",
    }:
        raise ValueError("unexpected certificate report shape")
    if (
        not isinstance(certificate["subject"], str)
        or "SignPath Foundation" not in certificate["subject"]
    ):
        raise ValueError("missing signing-certificate subject")
    thumbprint = certificate["thumbprint"]
    if not isinstance(thumbprint, str) or not _THUMBPRINT.fullmatch(thumbprint):
        raise ValueError("invalid signing-certificate thumbprint")
    not_after = certificate["not_after_utc"]
    if not isinstance(not_after, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", not_after
    ):
        raise ValueError("invalid signing-certificate expiry")

    timestamps = document["timestamp_authorities"]
    if not isinstance(timestamps, dict) or set(timestamps) != set(ARTIFACT_NAMES):
        raise ValueError("unexpected timestamp-authority set")
    for name in ARTIFACT_NAMES:
        timestamp = timestamps[name]
        if not isinstance(timestamp, dict) or set(timestamp) != {
            "subject",
            "thumbprint",
            "not_after_utc",
        }:
            raise ValueError(f"unexpected timestamp report shape: {name}")
        if not isinstance(timestamp["subject"], str) or not timestamp["subject"].strip():
            raise ValueError(f"missing timestamp authority: {name}")
        if not isinstance(timestamp["thumbprint"], str) or not _THUMBPRINT.fullmatch(
            timestamp["thumbprint"]
        ):
            raise ValueError(f"invalid timestamp thumbprint: {name}")
        if not isinstance(timestamp["not_after_utc"], str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp["not_after_utc"]
        ):
            raise ValueError(f"invalid timestamp expiry: {name}")

    reported_artifacts = document["artifacts"]
    if not isinstance(reported_artifacts, dict) or set(reported_artifacts) != set(
        ARTIFACT_NAMES
    ):
        raise ValueError("unexpected signed-artifact set")
    verified_hashes: dict[str, str] = {}
    for name in ARTIFACT_NAMES:
        expected = reported_artifacts[name]
        if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
            raise ValueError(f"invalid Authenticode artifact hash: {name}")
        path = artifacts[name]
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"signed artifact changed after verification: {name}")
        verified_hashes[name] = actual

    return {
        "status": "valid",
        "provider": document["provider"],
        "certificate_subject": certificate["subject"],
        "certificate_thumbprint": thumbprint,
        "certificate_not_after_utc": not_after,
        "timestamp_authorities": timestamps,
        "artifacts": verified_hashes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--gui", type=Path, default=Path("dist/StarCompanion.exe"))
    parser.add_argument(
        "--cli", type=Path, default=Path("dist/starcompanion-cli.exe")
    )
    args = parser.parse_args()
    result = verify(
        args.report,
        {"StarCompanion.exe": args.gui, "starcompanion-cli.exe": args.cli},
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
