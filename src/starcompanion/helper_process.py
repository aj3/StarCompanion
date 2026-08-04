"""Spawn-isolated execution for archive operations.

Only small progress/status messages cross the multiprocessing pipe.  Large
results are written to a parent-created, allowlisted artifact workspace and
decoded after the child exits, avoiding a second pickled result in the pipe.
"""

from __future__ import annotations

import json
import multiprocessing
import time
import traceback
from dataclasses import dataclass, field, replace
from enum import Enum

from . import cache
from .helper_artifacts import HelperArtifacts
from .inject import InjectionPlan, MergeMode
from .install import DEFAULT_LANGUAGE, GameInstall
from .fallbacks import FallbackDocument
from .tasks import CancellationToken, OperationCancelled, ProgressReporter
from .validate import Issue, Severity


class HelperOperation(Enum):
    READ_CONTRACTS = "read-contracts"
    PREPARE_UPDATE = "prepare-update"


@dataclass(frozen=True)
class HelperRequest:
    operation: HelperOperation
    install: GameInstall
    language: str = DEFAULT_LANGUAGE
    replacements: dict[str, str] = field(default_factory=dict)
    mode: MergeMode = MergeMode.MERGE
    artifacts: HelperArtifacts | None = None
    fallback_document: FallbackDocument | None = None
    allowed_additions: tuple[str, ...] = ()
    removals: tuple[str, ...] = ()
    allowed_removals: tuple[str, ...] = ()
    source_report: dict[str, object] | None = None


class HelperOperationError(RuntimeError):
    pass


def run_helper(
    request: HelperRequest,
    *,
    token: CancellationToken,
    reporter: ProgressReporter | None = None,
    cancel_grace_seconds: float = 1.0,
):
    """Run one request with file-backed results and bounded cancellation."""
    token.checkpoint()
    grace = max(0.0, cancel_grace_seconds)
    artifacts = HelperArtifacts.create()
    receive = None
    send = None
    process_cancelled = None
    process = None
    started = False
    ownership_transferred = False
    cancellation_deadline: float | None = None

    try:
        parent_replacements = request.replacements
        request = _stage_request(request, artifacts)
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process_cancelled = context.Event()
        process = context.Process(
            target=_child_main,
            args=(send, process_cancelled, request),
            name=f"StarCompanion-{request.operation.value}",
            daemon=True,
        )
        process.start()
        started = True
        send.close()

        while True:
            if token.is_cancelled:
                process_cancelled.set()
                if cancellation_deadline is None:
                    cancellation_deadline = time.monotonic() + grace
                if time.monotonic() >= cancellation_deadline:
                    process.terminate()
                    process.join()
                    raise OperationCancelled(
                        "archive helper terminated after cancellation timeout"
                    )

            if receive.poll(0.02):
                try:
                    kind, payload = receive.recv()
                except EOFError:
                    kind, payload = "eof", None
                if kind == "progress":
                    if reporter is not None:
                        reporter(payload)
                elif kind == "ready":
                    _finish_ready_process(process, grace)
                    if token.is_cancelled:
                        raise OperationCancelled("operation cancelled")
                    result, ownership_transferred = _load_result(
                        request, parent_replacements=parent_replacements
                    )
                    return result
                elif kind == "cancelled":
                    process.join(timeout=grace)
                    raise OperationCancelled(str(payload or "operation cancelled"))
                elif kind == "error":
                    process.join(timeout=grace)
                    error_type, message, child_traceback = payload
                    raise HelperOperationError(
                        f"{error_type}: {message}\n"
                        f"Child process traceback:\n{child_traceback}"
                    )

            if not process.is_alive():
                if receive.poll():
                    continue
                raise HelperOperationError(
                    f"archive helper exited unexpectedly with code {process.exitcode}"
                )
    finally:
        if receive is not None:
            receive.close()
        if send is not None:
            send.close()
        if started and process is not None and process.is_alive():
            process_cancelled.set()
            process.join(timeout=grace)
        if started and process is not None and process.is_alive():
            process.terminate()
            process.join()
        if not ownership_transferred:
            artifacts.cleanup()


def _finish_ready_process(process, grace: float) -> None:
    """A ready artifact is complete; ensure no child remains behind."""
    process.join(timeout=grace)
    if process.is_alive():
        process.terminate()
        process.join()


def _load_result(
    request: HelperRequest,
    *,
    parent_replacements: dict[str, str],
):
    artifacts = _require_artifacts(request)
    if not artifacts.result.is_file():
        raise HelperOperationError("archive helper reported ready without a result artifact")

    if request.operation is HelperOperation.READ_CONTRACTS:
        with artifacts.result.open("r", encoding="utf-8") as stream:
            result = cache.load_lines(stream)
        artifacts.cleanup()
        return result, False

    if request.operation is HelperOperation.PREPARE_UPDATE:
        from .operations import PreparedUpdate
        from .prepare import BaselineSource, PreparedLocalization

        source, plan, integrity_warnings = _read_prepared_result(artifacts.result)
        if not artifacts.baseline.is_file():
            raise HelperOperationError(
                "archive helper reported ready without a baseline artifact"
            )
        localization = PreparedLocalization(
            install=request.install,
            language=request.language,
            baseline_path=artifacts.baseline,
            source=BaselineSource(source),
            mode=request.mode,
            prepared_target_fingerprint=plan.target_fingerprint,
            artifacts=artifacts,
            integrity_warnings=integrity_warnings,
        )
        result = PreparedUpdate(
            localization=localization,
            replacements=parent_replacements,
            plan=plan,
            allowed_additions=frozenset(request.allowed_additions),
            removals=frozenset(request.removals),
            allowed_removals=frozenset(request.allowed_removals),
        )
        artifacts.discard_transient()
        return result, True

    raise HelperOperationError(f"unsupported helper operation: {request.operation}")


def _child_main(connection, cancelled_event, request: HelperRequest) -> None:
    token = CancellationToken(cancelled_event)
    prepared = None

    def send_progress(event) -> None:
        connection.send(("progress", event))

    try:
        from .operations import _prepare_update_local, _read_contracts_local

        artifacts = _require_artifacts(request)
        if request.operation is HelperOperation.READ_CONTRACTS:
            contracts = _read_contracts_local(
                request.install,
                language=request.language,
                token=token,
                reporter=send_progress,
                datacore_path=artifacts.datacore,
                fallback_document=request.fallback_document,
            )
            token.checkpoint()
            with artifacts.result.open("w", encoding="utf-8", newline="\n") as stream:
                cache.dump_lines(contracts, stream, source="archive-helper")
        elif request.operation is HelperOperation.PREPARE_UPDATE:
            replacements, source_report = _load_prepare_input(artifacts)
            prepared = _prepare_update_local(
                request.install,
                replacements,
                mode=request.mode,
                language=request.language,
                token=token,
                reporter=send_progress,
                baseline_path=artifacts.baseline,
                allowed_additions=frozenset(request.allowed_additions),
                removals=frozenset(request.removals),
                allowed_removals=frozenset(request.allowed_removals),
                source_report=source_report,
            )
            token.checkpoint()
            _write_prepared_result(prepared, artifacts.result)
        else:  # pragma: no cover - enum construction prevents this in callers
            raise ValueError(f"unsupported helper operation: {request.operation}")
        connection.send(("ready", None))
    except OperationCancelled as exc:
        _cleanup_prepared(prepared)
        connection.send(("cancelled", str(exc)))
    except BaseException as exc:
        _cleanup_prepared(prepared)
        try:
            connection.send(
                (
                    "error",
                    (type(exc).__name__, str(exc), traceback.format_exc()),
                )
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        connection.close()


def _require_artifacts(request: HelperRequest) -> HelperArtifacts:
    if request.artifacts is None:
        raise ValueError("helper request has no parent-owned artifact workspace")
    request.artifacts.validate()
    return request.artifacts


def _stage_request(
    request: HelperRequest,
    artifacts: HelperArtifacts,
) -> HelperRequest:
    """Put potentially large request fields on disk before spawning."""
    if request.operation is HelperOperation.PREPARE_UPDATE:
        with artifacts.input.open("w", encoding="utf-8", newline="\n") as stream:
            _write_json_line(
                stream,
                {"type": "source-report", "value": request.source_report},
            )
            for key, value in request.replacements.items():
                _write_json_line(
                    stream, {"type": "replacement", "key": key, "value": value}
                )
    return replace(
        request, replacements={}, source_report=None, artifacts=artifacts
    )


def _load_prepare_input(
    artifacts: HelperArtifacts,
) -> tuple[dict[str, str], dict[str, object] | None]:
    if not artifacts.input.is_file():
        raise ValueError("prepare helper has no parent-owned input artifact")
    data: dict[str, str] = {}
    source_report = None
    with artifacts.input.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            item = json.loads(line)
            kind = item.get("type")
            if number == 1 and kind == "source-report":
                source_report = item.get("value")
                if source_report is not None and not isinstance(source_report, dict):
                    raise ValueError("prepare helper source report must be an object")
                continue
            key, value = item.get("key"), item.get("value")
            if kind != "replacement" or not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(
                    f"prepare helper input line {number} must contain string key/value"
                )
            data[key] = value
    return data, source_report


def _cleanup_prepared(prepared) -> None:
    localization = getattr(prepared, "localization", None)
    cleanup = getattr(localization, "cleanup", None)
    if cleanup is not None:
        cleanup()


def _write_prepared_result(prepared, path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        _write_json_line(
            stream,
            {
                "type": "header",
                "result_version": 3,
                "source": prepared.localization.source.value,
                "plan": prepared.plan.to_dict(),
                "integrity_warnings": list(prepared.localization.integrity_warnings),
            },
        )


def _read_prepared_result(path) -> tuple[str, InjectionPlan, tuple[str, ...]]:
    source = None
    plan = None
    integrity_warnings: tuple[str, ...] = ()
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            item = json.loads(line)
            kind = item.get("type")
            if number == 1:
                if kind != "header" or item.get("result_version") not in (1, 2, 3):
                    raise HelperOperationError("unsupported prepared-result header")
                source = item["source"]
                if item["result_version"] in (2, 3):
                    plan = InjectionPlan.from_dict(item.get("plan"))
                else:
                    plan = InjectionPlan()
                if item["result_version"] == 3:
                    raw_warnings = item.get("integrity_warnings", [])
                    if not isinstance(raw_warnings, list) or not all(
                        isinstance(warning, str) for warning in raw_warnings
                    ):
                        raise HelperOperationError("invalid integrity warnings in result")
                    integrity_warnings = tuple(raw_warnings)
            elif kind in ("added", "updated", "removed", "unchanged", "skipped"):
                getattr(plan, kind).append(item["key"])
            elif kind in ("errors", "warnings"):
                getattr(plan, kind).append(
                    (item["key"], _issue_from_dict(item["issue"]))
                )
            else:
                raise HelperOperationError(
                    f"unknown prepared-result record {kind!r} on line {number}"
                )
    if source is None or plan is None:
        raise HelperOperationError("prepared-result artifact is empty")
    return source, plan, integrity_warnings


def _issue_to_dict(issue: Issue) -> dict:
    return {
        "severity": issue.severity.value,
        "code": issue.code,
        "message": issue.message,
        "offset": issue.offset,
    }


def _issue_from_dict(data: dict) -> Issue:
    return Issue(
        severity=Severity(data["severity"]),
        code=data["code"],
        message=data["message"],
        offset=data.get("offset"),
    )


def _write_json_line(stream, data: dict) -> None:
    json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")


__all__ = [
    "HelperOperation",
    "HelperOperationError",
    "HelperRequest",
    "run_helper",
]
