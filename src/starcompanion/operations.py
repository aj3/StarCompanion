"""Headless long-running use cases shared by GUI and future CLI workflows."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .inject import InjectionPlan, MergeMode, apply
from .ini import LocalizationFile
from .install import DEFAULT_LANGUAGE, GameInstall
from .model import ContractSet
from .fallbacks import FallbackDocument
from .prepare import PreparedLocalization, prepare_localization, stream_stock_localization
from .sources import game_strings
from .tasks import (
    CancellationToken,
    OperationStage,
    ProgressReporter,
    report,
)
from .transactions import TransactionJournal


@dataclass(frozen=True)
class PreparedUpdate:
    localization: PreparedLocalization
    replacements: dict[str, str]
    plan: InjectionPlan
    allowed_additions: frozenset[str] = frozenset()
    removals: frozenset[str] = frozenset()
    allowed_removals: frozenset[str] = frozenset()

    def commit(
        self,
        *,
        confirmed: bool,
        backup_dir: Path | None = None,
        journal: TransactionJournal | None = None,
    ) -> InjectionPlan:
        result = apply(
            self.localization.target,
            self.replacements,
            confirmed=confirmed,
            source=self.localization.baseline(),
            backup_dir=backup_dir,
            allowed_additions=self.allowed_additions,
            removals=self.removals,
            allowed_removals=self.allowed_removals,
            expected_fingerprint=self.plan.target_fingerprint,
            operation_plan=self.plan,
            journal=journal,
        )
        self.plan.backup = result.backup
        self.plan.transaction_status = result.transaction_status
        self.plan.diagnostics = list(result.diagnostics)
        return self.plan

    def cleanup(self) -> None:
        self.localization.cleanup()

    def __enter__(self) -> PreparedUpdate:
        return self

    def __exit__(self, *_exc_info) -> None:
        self.cleanup()


def read_contracts(
    install: GameInstall,
    *,
    language: str = DEFAULT_LANGUAGE,
    token: CancellationToken | None = None,
    reporter: ProgressReporter | None = None,
    isolated: bool = True,
    cancel_grace_seconds: float = 1.0,
    fallback_document: FallbackDocument | None = None,
) -> ContractSet:
    token = token or CancellationToken()
    if fallback_document is not None:
        fallback_document.validate_context(
            game_version=install.version,
            language=language,
        )
    if isolated:
        from .helper_process import (
            HelperOperation,
            HelperRequest,
            run_helper,
        )

        return run_helper(
            HelperRequest(
                HelperOperation.READ_CONTRACTS,
                install,
                language,
                fallback_document=fallback_document,
            ),
            token=token,
            reporter=reporter,
            cancel_grace_seconds=cancel_grace_seconds,
        )
    return _read_contracts_local(
        install,
        language=language,
        token=token,
        reporter=reporter,
        fallback_document=fallback_document,
    )


def _read_contracts_local(
    install: GameInstall,
    *,
    language: str = DEFAULT_LANGUAGE,
    token: CancellationToken,
    reporter: ProgressReporter | None = None,
    datacore_path: Path | None = None,
    fallback_document: FallbackDocument | None = None,
) -> ContractSet:
    from .enhancements import (
        MissionEnhancementProvider,
        apply_enhancements,
        unavailable_mission_enhancements,
    )
    from .extract import datacore, dataforge
    from .extract.p4k import P4KArchive, is_localization_entry
    from .fallbacks import apply_to_localization, record_usage

    owns_datacore = datacore_path is None
    if datacore_path is None:
        descriptor, name = tempfile.mkstemp(prefix="starcompanion-", suffix="-Game2.dcb")
        os.close(descriptor)
        datacore_path = Path(name)
    data_entry = "Data/Game2.dcb"
    try:
        report(reporter, OperationStage.OPEN_ARCHIVE, f"Opening {install.archive.name}…")

        def index_progress(current: int, total: int) -> None:
            report(
                reporter,
                OperationStage.INDEX_ARCHIVE,
                "Reading the game archive index…",
                current,
                total,
            )

        read_stage = OperationStage.READ_LOCALIZATION

        phase_bounds = {
            "read": (0, 450),
            "decrypt": (450, 650),
            "decompress": (650, 1000),
        }

        def entry_progress(phase: str, current: int, total: int) -> None:
            stage = read_stage
            start, end = phase_bounds[phase]
            position = min(1.0, max(0.0, current / total)) if total else 1.0
            combined = round(start + ((end - start) * position))
            message = (
                "Extracting local mission facts…"
                if stage is OperationStage.READ_DATACORE
                else "Reading stock localization…"
            )
            report(reporter, stage, message, combined, 1000)

        wanted = lambda entry: (
            is_localization_entry(entry)
            or entry.filename.casefold() == data_entry.casefold()
        )
        with tempfile.SpooledTemporaryFile(max_size=1 << 20, mode="w+b") as stream:
            with P4KArchive(
                install.archive,
                progress=index_progress,
                entry_progress=entry_progress,
                checkpoint=token.checkpoint,
                entry_filter=wanted,
            ) as archive:
                archive.stream_localization(stream.write, language)
                if data_entry in archive:
                    read_stage = OperationStage.READ_DATACORE
                    archive.extract(data_entry, datacore_path)
                    has_datacore = True
                else:
                    has_datacore = False
            token.checkpoint()
            strings = LocalizationFile.load_stream(stream)
        token.checkpoint()

        facts = None
        if not has_datacore:
            enhancement_set = unavailable_mission_enhancements(
                install.version or "unknown",
                "Data/Game2.dcb is not present in this archive",
            )
        else:
            try:
                report(
                    reporter,
                    OperationStage.PARSE_DATACORE,
                    "Resolving local mission reward records…",
                )
                facts = dataforge.extract_mission_facts(datacore.load(datacore_path))
                token.checkpoint()
            except datacore.DataCoreError as exc:
                enhancement_set = unavailable_mission_enhancements(
                    install.version or "unknown",
                    f"Game2.dcb could not be read: {exc}",
                )
        evidenced_groups = (
            tuple(
                game_strings.MissionKeyEvidence(
                    fact.title_keys,
                    fact.description_keys,
                )
                for fact in facts.facts
            )
            if facts is not None
            else ()
        )
        applied_fallbacks = None
        if fallback_document is not None:
            if facts is None:
                raise ValueError(
                    "user fallbacks require a readable Data/Game2.dcb for exact-key validation"
                )
            applied_fallbacks = apply_to_localization(
                strings,
                facts,
                fallback_document,
            )
        report(reporter, OperationStage.PARSE_CONTRACTS, "Finding contract strings…")
        contracts = game_strings.parse(strings, evidenced_groups=evidenced_groups)
        token.checkpoint()
        if facts is not None:
            enhancement_set = MissionEnhancementProvider(strings.get).build(facts)
        report(
            reporter,
            OperationStage.APPLY_ENHANCEMENTS,
            "Merging local mission rewards…",
        )
        contracts = apply_enhancements(contracts, [enhancement_set])
        if applied_fallbacks is not None:
            record_usage(
                contracts,
                applied_fallbacks,
                game_version=install.version or "unknown",
            )
        token.checkpoint()
        report(reporter, OperationStage.COMPLETE, "Game strings are ready.", 1, 1)
        return contracts
    finally:
        if owns_datacore:
            datacore_path.unlink(missing_ok=True)


def prepare_update(
    install: GameInstall,
    replacements: dict[str, str],
    *,
    mode: MergeMode = MergeMode.MERGE,
    language: str = DEFAULT_LANGUAGE,
    token: CancellationToken | None = None,
    reporter: ProgressReporter | None = None,
    isolated: bool = True,
    cancel_grace_seconds: float = 1.0,
    allowed_additions: set[str] | frozenset[str] = frozenset(),
    removals: set[str] | frozenset[str] = frozenset(),
    allowed_removals: set[str] | frozenset[str] = frozenset(),
    source_report: dict[str, object] | None = None,
) -> PreparedUpdate:
    token = token or CancellationToken()
    if isolated:
        from .helper_process import (
            HelperOperation,
            HelperRequest,
            run_helper,
        )

        return run_helper(
            HelperRequest(
                operation=HelperOperation.PREPARE_UPDATE,
                install=install,
                language=language,
                replacements=replacements,
                mode=mode,
                allowed_additions=tuple(sorted(allowed_additions)),
                removals=tuple(sorted(removals)),
                allowed_removals=tuple(sorted(allowed_removals)),
                source_report=source_report,
            ),
            token=token,
            reporter=reporter,
            cancel_grace_seconds=cancel_grace_seconds,
        )
    return _prepare_update_local(
        install,
        replacements,
        mode=mode,
        language=language,
        token=token,
        reporter=reporter,
        allowed_additions=allowed_additions,
        removals=removals,
        allowed_removals=allowed_removals,
        source_report=source_report,
    )


def _prepare_update_local(
    install: GameInstall,
    replacements: dict[str, str],
    *,
    mode: MergeMode,
    language: str,
    token: CancellationToken,
    reporter: ProgressReporter | None = None,
    baseline_path: Path | None = None,
    allowed_additions: set[str] | frozenset[str] = frozenset(),
    removals: set[str] | frozenset[str] = frozenset(),
    allowed_removals: set[str] | frozenset[str] = frozenset(),
    source_report: dict[str, object] | None = None,
) -> PreparedUpdate:
    localization = prepare_localization(
        install,
        language=language,
        mode=mode,
        token=token,
        reporter=reporter,
        temporary_path=baseline_path,
    )
    token.checkpoint()
    report(reporter, OperationStage.PREVIEW_CHANGES, "Calculating the change preview…")
    try:
        result = localization.operation_plan(
            replacements,
            allowed_additions=allowed_additions,
            removals=removals,
            allowed_removals=allowed_removals,
            source_report=source_report,
        )
        token.checkpoint()
        report(reporter, OperationStage.COMPLETE, "Preview is ready.", 1, 1)
        return PreparedUpdate(
            localization,
            replacements,
            result,
            frozenset(allowed_additions),
            frozenset(removals),
            frozenset(allowed_removals),
        )
    except BaseException:
        localization.cleanup()
        raise


__all__ = ["PreparedUpdate", "prepare_update", "read_contracts"]
