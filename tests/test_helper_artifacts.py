from pathlib import Path
from threading import Event

import pytest

from starcompanion.helper_artifacts import HelperArtifacts
from starcompanion.helper_process import (
    HelperOperation,
    HelperRequest,
    _child_main,
    _stage_request,
)
from starcompanion.install import GameInstall

from test_operations import make_install


def test_artifact_paths_must_stay_directly_under_parent_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    artifacts = HelperArtifacts(
        root=root,
        baseline=tmp_path / "escaped.ini",
        input=root / "input.jsonl",
        result=root / "result.jsonl",
    )

    with pytest.raises(ValueError, match="escapes"):
        artifacts.validate()


def test_cleanup_removes_only_declared_files_and_empty_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    artifacts = HelperArtifacts(
        root, root / "baseline.ini", root / "input.jsonl", root / "result.jsonl"
    )
    for path in artifacts.paths:
        path.write_bytes(b"x")

    artifacts.cleanup()
    artifacts.cleanup()

    assert not root.exists()


def test_unknown_child_artifact_is_not_recursively_deleted(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    artifacts = HelperArtifacts(
        root, root / "baseline.ini", root / "input.jsonl", root / "result.jsonl"
    )
    unknown = root / "not-declared.bin"
    unknown.write_bytes(b"keep")

    with pytest.raises(OSError):
        artifacts.cleanup()

    assert unknown.read_bytes() == b"keep"


def test_child_protocol_sends_status_not_contract_payload(tmp_path):
    install = make_install(tmp_path)
    root = tmp_path / "workspace"
    root.mkdir()
    artifacts = HelperArtifacts(
        root, root / "baseline.ini", root / "input.jsonl", root / "result.jsonl"
    )
    sent = []

    class Connection:
        def send(self, message):
            sent.append(message)

        def close(self):
            pass

    _child_main(
        Connection(),
        Event(),
        HelperRequest(HelperOperation.READ_CONTRACTS, install, artifacts=artifacts),
    )

    assert sent[-1] == ("ready", None)
    assert artifacts.result.is_file()
    assert all(type(payload).__name__ != "ContractSet" for _, payload in sent)
    artifacts.cleanup()


def test_large_prepare_input_is_staged_and_removed_from_spawn_request(tmp_path):
    install = GameInstall(tmp_path, "LIVE")
    root = tmp_path / "workspace"
    root.mkdir()
    artifacts = HelperArtifacts(
        root, root / "baseline.ini", root / "input.jsonl", root / "result.jsonl"
    )
    replacements = {f"key-{index}": "value" * 100 for index in range(100)}

    child_request = _stage_request(
        HelperRequest(
            HelperOperation.PREPARE_UPDATE,
            install,
            replacements=replacements,
        ),
        artifacts,
    )

    assert child_request.replacements == {}
    assert child_request.artifacts == artifacts
    assert artifacts.input.stat().st_size > 10_000
    artifacts.cleanup()
