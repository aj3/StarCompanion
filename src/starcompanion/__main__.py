"""Command line interface.

Anything that writes to the game file is gated. The preferred `channel`
workflow prepares directly from an install: `preview` is read-only, while
`apply` and `rollback` refuse without `--confirm`. The lower-level `apply`
also needs `--allow-game-folder` for a target inside a real install.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import cache
from .blueprints import BlueprintQuery, OwnershipFilter, build_catalog, query_blueprints
from .config import Profile, builtin_profiles, load_builtin
from .extract import datacore, dataforge
from .diagnostics import build_diagnostics, render_diagnostics, write_diagnostics
from .extract.p4k import P4KArchive, P4KError, is_localization_entry
from .fallbacks import FallbackDocument, SCHEMA_VERSION, template_from_contracts
from .ini import LocalizationFile
from .inject import (
    InjectionPlan,
    MergeMode,
    UnconfirmedWriteError,
    ValidationFailedError,
    apply,
    backup,
    looks_like_game_install,
    restore,
)
from .inject import plan as plan_injection
from . import install as installs
from .operations import prepare_update, read_contracts
from .prepare import stream_stock_localization
from .source_graph import SourceGraph, SourceKind, SourceLayer, report as source_report
from .sources import contracts_ini, game_strings, merge, scmdb
from .user_edits import (
    ConflictChoice,
    EditCommand,
    EditSession,
    UserEditStore,
    data_dir,
    load_ini as load_user_ini,
    plan_import,
)
from .validate import Severity, validate_value
from .transactions import TransactionJournal, bytes_sha256, fingerprint
from .ownership import (
    OwnershipStore,
    apply_import as apply_ownership_import,
    apply_resolution as apply_ownership_resolution,
    discover_log_files,
    export_csv as export_ownership_csv,
    export_json as export_ownership_json,
    plan_import as plan_ownership_import,
    plan_resolution as plan_ownership_resolution,
    scan_logs,
    write_export as write_ownership_export,
)
from .portability import (
    LanguagePackStore,
    PreferencesStore,
    apply_settings_import,
    load_language_pack,
    plan_settings_export,
    plan_settings_import,
    recover_settings_restore,
    settings_recovery_status,
    write_settings_archive,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 3
EXIT_INVALID = 4
MAX_SOURCE_REPORT_BYTES = 128 * 1024 * 1024

def _resolve_profile(value: str | None) -> Profile:
    if value is None:
        return load_builtin("default")
    path = Path(value)
    return Profile.load(path) if path.exists() else load_builtin(value)


def _load_rendered(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} should hold an object of key -> value")
    return data


def _load_source_report(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    if path.stat().st_size > MAX_SOURCE_REPORT_BYTES:
        raise ValueError(f"{path} exceeds the source-report size limit")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} should hold a source report object")
    precedence = data.get("precedence")
    entries = data.get("entries")
    if not isinstance(precedence, list) or not all(
        isinstance(item, str) for item in precedence
    ):
        raise ValueError(f"{path} has invalid source precedence")
    if not isinstance(entries, dict):
        raise ValueError(f"{path} has no source entries")
    sanitized: dict[str, dict[str, object]] = {}
    for key, entry in entries.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise ValueError(f"{path} contains an invalid source entry")
        winner = entry.get("winner")
        winner_kind = entry.get("winner_kind")
        conflicted = entry.get("conflicted")
        if (
            not isinstance(winner, str)
            or not isinstance(winner_kind, str)
            or not isinstance(conflicted, bool)
        ):
            raise ValueError(f"{path} source entry {key!r} lacks winner metadata")
        sanitized[key] = {
            "winner": winner,
            "winner_kind": winner_kind,
            "conflicted": conflicted,
        }
    return {"precedence": list(precedence), "entries": sanitized}


def _load_fallbacks(path: Path | None) -> FallbackDocument | None:
    return FallbackDocument.load(path) if path is not None else None


def _cache_game_version(path: Path) -> str | None:
    source = str(cache.describe(path).get("source") or "")
    parts = source.split(":")
    return parts[2] if len(parts) >= 3 and parts[0] == "game" else None


def _cache_channel(path: Path) -> str | None:
    source = str(cache.describe(path).get("source") or "")
    parts = source.split(":")
    return parts[1] if len(parts) >= 3 and parts[0] == "game" else None


def _localization_values(path: Path) -> dict[str, str]:
    parsed = LocalizationFile.load(path)
    values: dict[str, str] = {}
    for entry in parsed.entries():
        if entry.key in values:
            raise ValueError(f"duplicate localization key {entry.key!r} in {path}")
        values[entry.key] = entry.value
    return values


def _cache_source(path: Path) -> str:
    return str(cache.describe(path).get("source") or path.name)


def _contract_stock_values(contracts, *, keys: set[str] | None = None) -> dict[str, str]:
    return {
        key: contract.base_text(key) or contract.text(key) or ""
        for contract in contracts.contracts
        for key in contract.all_keys()
        if keys is None or key in keys
    }


def _user_store(args, channel: str) -> UserEditStore:
    return UserEditStore(channel, args.language, root=getattr(args, "data_root", None))


def _transaction_journal(args, target: Path) -> TransactionJournal:
    state_dir = _channel_backup_dir(args, target)
    return TransactionJournal(
        state_dir / ".apply-journal.json",
        state_dir / "last-operation.json",
    )


def _resolve_install(path: Path | None):
    game = installs.identify(path) if path is not None else installs.find_default()
    if game is None:
        choice = f"{path}" if path is not None else "the default locations"
        raise ValueError(f"no Star Citizen install containing Data.p4k found at {choice}")
    return game


def _channel_backup_dir(args, target: Path) -> Path:
    if args.backup_dir is None:
        return target.parent / "backups"
    try:
        channel = installs.normalize_channel(target.parents[3].name)
        language = installs.normalize_language(target.parent.name)
    except (IndexError, ValueError) as exc:
        raise ValueError("cannot derive channel/language scope for custom backup root") from exc
    return Path(args.backup_dir) / channel / language


def _channel_backups(directory: Path, target: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.glob(f"{target.stem}.*{target.suffix}") if path.is_file()),
        reverse=True,
    )


def _print_prepared(prepared, mode: MergeMode, *, limit: int) -> None:
    plan = prepared.plan
    limit = max(0, limit)
    print(f"game     : {prepared.localization.install.label}")
    print(f"language : {prepared.localization.language}")
    print(f"target   : {prepared.localization.target}")
    print(f"baseline : {prepared.localization.source.value}")
    print(f"mode     : {mode.value}")
    print(f"plan id  : {plan.plan_id or 'unbound'}")
    if plan.target_fingerprint is not None:
        state = (
            f"{plan.target_fingerprint.sha256[:12]}…"
            if plan.target_fingerprint.exists and plan.target_fingerprint.sha256
            else "missing override"
        )
        print(f"target id: {state}")
    if plan.desired_sha256:
        print(f"result id: {plan.desired_sha256[:12]}…")
    print(f"plan     : {plan.summary()}")
    for warning in prepared.localization.integrity_warnings:
        print(f"  integrity warning: {warning}", file=sys.stderr)
    for key in plan.added[:limit]:
        print(f"  added   {key}: explicitly authorized user fallback")
    for key in plan.removed[:limit]:
        print(f"  removed {key}: absent from the prepared result")
    for key in plan.skipped[:limit]:
        print(f"  skipped {key}: no matching localization key", file=sys.stderr)
    for key, issue in plan.warnings[:limit]:
        print(f"  warning {key}: {issue}", file=sys.stderr)
    for key, issue in plan.errors[:limit]:
        print(f"  error {key}: {issue}", file=sys.stderr)


def _prepare_channel(args):
    game = _resolve_install(args.install)
    mode = MergeMode(args.mode)
    fallback_document = _load_fallbacks(args.fallbacks)
    if fallback_document is not None:
        fallback_document.validate_context(
            game_version=game.version,
            language=args.language,
        )
    allowed_additions = set(
        fallback_document.authored_values if fallback_document is not None else ()
    )
    replacements = _load_rendered(args.rendered)
    user_store = _user_store(args, game.channel)
    if not args.no_user_edits:
        replacements.update(user_store.load())
    return mode, prepare_update(
        game,
        replacements,
        mode=mode,
        language=args.language,
        allowed_additions=allowed_additions,
        removals=frozenset(args.remove_key or ()),
        allowed_removals=frozenset(allowed_additions),
        source_report=_load_source_report(args.sources),
    ), user_store


# --- commands ----------------------------------------------------------------


def cmd_import(args) -> int:
    """Read contracts. The game's own strings are the default source; a
    community contract list only adds reward values on top."""
    if args.contracts:
        if args.fallbacks is not None:
            raise ValueError("--fallbacks requires a game import, not --contracts")
        contracts = contracts_ini.load(args.contracts)
        source = f"contracts.ini:{args.contracts.name}"
    else:
        game = installs.identify(args.install) if args.install else installs.find_default()
        if game is None:
            print(
                "error: no Star Citizen install found. Pass --install <game folder>, "
                "or --contracts <file> to read a community contract list instead.",
                file=sys.stderr,
            )
            return EXIT_ERROR

        print(f"reading the game's own strings from {game.label}")
        fallback_document = _load_fallbacks(args.fallbacks)
        contracts = read_contracts(
            game,
            language=args.language,
            fallback_document=fallback_document,
        )
        source = f"game:{game.channel}:{game.version or 'unknown'}"

    cache.save(contracts, args.out, source=source)

    print(f"imported {len(contracts.contracts)} contracts from {len(contracts.orgs)} orgs")
    print(f"  keys     : {sum(len(c.all_keys()) for c in contracts.contracts)}")
    for capability in contracts.capabilities:
        print(
            f"  provider : {capability.provider} v{capability.version} "
            f"{capability.status.value} — {capability.contracts_enhanced:,} contracts, "
            f"{capability.evidence_links:,} evidence links"
        )
        print(
            f"             reward facts {capability.matched_facts:,}/"
            f"{capability.reward_facts:,} matched; "
            f"{capability.unmatched_facts:,} unmatched"
        )
        if capability.diagnostic_counts:
            print(
                "             diagnostics "
                + ", ".join(
                    f"{category}={count:,}"
                    for category, count in capability.diagnostic_counts
                )
            )
        if capability.unmatched_reason_counts:
            print(
                "             unmatched "
                + ", ".join(
                    f"{reason}={count:,}"
                    for reason, count in capability.unmatched_reason_counts
                )
            )
        if capability.diagnostics:
            print(f"             {capability.diagnostics[0]}")
    if contracts.unparsed:
        print(f"  unparsed : {len(contracts.unparsed)} (no reward data found)")
    print(f"  written  : {args.out}")
    return EXIT_OK


def cmd_render(args) -> int:
    contracts = cache.load(args.cache)
    profile = _resolve_profile(args.profile)

    for problem in profile.validate_against(contracts):
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_INVALID

    result = profile.build_renderer().render_all(contracts)
    stock_values = _contract_stock_values(contracts, keys=set(result.values))
    graph = SourceGraph(
        [
            SourceLayer(
                f"stock:{_cache_source(args.cache)}",
                SourceKind.STOCK,
                stock_values,
            ),
        ]
    )
    channel = args.channel or _cache_channel(args.cache)
    if channel is not None and not args.no_language_pack:
        local_pack = LanguagePackStore(channel, args.language, args.data_root).load()
        if local_pack:
            graph.add(
                SourceLayer(
                    f"language-pack:{channel.upper()}:{args.language.casefold()}",
                    SourceKind.LANGUAGE_OVERLAY,
                    local_pack,
                    order=0,
                )
            )
    if args.language_overlay is not None:
        graph.add(
            SourceLayer(
                f"language-overlay:{args.language_overlay.name}",
                SourceKind.LANGUAGE_OVERLAY,
                _localization_values(args.language_overlay),
                order=1,
            )
        )
    for number, path in enumerate(args.import_source or ()):
        graph.add(
            SourceLayer(
                f"import:{number + 1}:{path.name}",
                SourceKind.IMPORT,
                _localization_values(path),
                order=number,
            )
        )
    generated_provenance = {
        key: tuple(
            f"{item.provider}:{item.record_id}:{item.field_path}"
            for item in evidence
        )
        for key, evidence in result.provenance.items()
    }
    graph.add(
        SourceLayer(
            "generated-enhancements",
            SourceKind.GENERATED,
            result.values,
            provenance=generated_provenance,
        )
    )
    active_user_store = None
    if channel is not None and not args.no_user_edits:
        active_user_store = _user_store(args, channel)
        graph.add(
            SourceLayer(
                f"user:{channel.upper()}:{args.language.casefold()}",
                SourceKind.USER,
                active_user_store.load(),
            )
        )
    merged = graph.resolve()
    merge_errors = [
        (key, issue)
        for key, entry in merged.entries.items()
        for issue in validate_value(entry.value, trusted_source=stock_values.get(key, ""))
        if issue.severity is Severity.ERROR
    ]
    invalid_keys = {key for key, _issue in merge_errors}
    safe_values = {
        key: value for key, value in merged.values.items() if key not in invalid_keys
    }
    args.out.write_text(json.dumps(safe_values, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.provenance_out:
        provenance = {
            key: [
                {
                    "provider": item.provider,
                    "record_id": item.record_id,
                    "record_path": item.record_path,
                    "field_path": item.field_path,
                    "value": item.value,
                }
                for item in evidence
            ]
            for key, evidence in result.provenance.items()
        }
        args.provenance_out.write_text(
            json.dumps(provenance, indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.sources_out:
        args.sources_out.write_text(
            json.dumps(source_report(merged), indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
    if args.conflicts_out:
        full_report = source_report(merged)
        conflict_entries = {
            key: full_report["entries"][key] for key in merged.conflicts
        }
        args.conflicts_out.write_text(
            json.dumps(
                {
                    "precedence": full_report["precedence"],
                    "conflict_count": len(conflict_entries),
                    "entries": conflict_entries,
                },
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    print(f"rendered with profile '{profile.name}': {result.summary()}")
    print(f"  sources : {len(merged.layers)} layers, {len(merged.conflicts)} conflicts")
    if active_user_store is not None:
        print(f"  user.ini: {active_user_store.path}")
    for key, reason in result.skipped[:10]:
        print(f"  skipped {key}: {reason}", file=sys.stderr)
    for key, issue in merge_errors[:10]:
        print(f"  invalid {key}: {issue}", file=sys.stderr)
    print(f"  written : {args.out}")
    if args.provenance_out:
        print(f"  evidence: {args.provenance_out}")
    if args.sources_out:
        print(f"  sources : {args.sources_out}")
    if args.conflicts_out:
        print(f"  conflicts: {args.conflicts_out}")
    return EXIT_INVALID if result.skipped or merge_errors else EXIT_OK


def cmd_plan(args) -> int:
    target = LocalizationFile.load(args.target)
    fallback_document = _load_fallbacks(args.fallbacks)
    allowed_additions = set(
        fallback_document.authored_values if fallback_document is not None else ()
    )
    result = plan_injection(
        target,
        _load_rendered(args.rendered),
        allowed_additions=allowed_additions,
    )

    print(f"target : {args.target}")
    print(f"plan   : {result.summary()}")

    for key in result.added[:10]:
        print(f"  authorized addition: {key}")

    for key in result.skipped[:10]:
        print(f"  no such key in target: {key}", file=sys.stderr)
    for key, issue in result.errors[:10]:
        print(f"  {key}: {issue}", file=sys.stderr)

    print("\nnothing was written; re-run with `apply --confirm` to write")
    return EXIT_INVALID if result.errors else EXIT_OK


def cmd_apply(args) -> int:
    replacements = _load_rendered(args.rendered)
    fallback_document = _load_fallbacks(args.fallbacks)
    allowed_additions = set(
        fallback_document.authored_values if fallback_document is not None else ()
    )

    install = looks_like_game_install(args.target)
    if install and not args.allow_game_folder:
        print(
            f"refusing to write: {args.target} is inside what looks like a Star Citizen\n"
            f"install ({install}). Re-run with --allow-game-folder if that is intended.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    profile = _resolve_profile(args.profile)
    mode = MergeMode(args.mode) if args.mode else profile.injection.merge_mode

    if mode is MergeMode.OVERWRITE and args.stock is None:
        print("error: --mode overwrite needs --stock (a pristine global.ini)", file=sys.stderr)
        return EXIT_INVALID

    try:
        result = apply(
            args.target,
            replacements,
            confirmed=args.confirm,
            mode=mode,
            stock_path=args.stock,
            backup_dir=args.backup_dir,
            allowed_additions=allowed_additions,
        )
    except UnconfirmedWriteError:
        preview = plan_injection(
            LocalizationFile.load(args.target),
            replacements,
            allowed_additions=allowed_additions,
        )
        print(f"would change: {preview.summary()}")
        print("refusing to write without --confirm", file=sys.stderr)
        return EXIT_REFUSED
    except ValidationFailedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for key, issue in exc.failures[:10]:
            print(f"  {key}: {issue}", file=sys.stderr)
        return EXIT_INVALID

    print(f"applied ({mode.value}): {result.summary()}")
    print(f"  target : {args.target}")
    print(f"  backup : {args.backup_dir or args.target.parent / 'backups'}")
    return EXIT_OK


def cmd_channel_preview(args) -> int:
    mode, prepared, user_store = _prepare_channel(args)
    with prepared:
        _print_prepared(prepared, mode, limit=args.limit)
        journal = _transaction_journal(args, prepared.localization.target)
        recovery = journal.inspect(prepared.localization.target)
        print(f"recovery : {recovery.status} — {recovery.message}")
        if not args.no_user_edits:
            print(f"user.ini : {user_store.path} ({len(user_store.load())} overrides)")
        if args.plan_out:
            prepared.plan.save(args.plan_out)
            print(f"plan file: {args.plan_out}")
        print("\nnothing was written; use `channel apply --confirm` to write")
        return EXIT_INVALID if prepared.plan.errors or recovery.needs_attention else EXIT_OK


def cmd_channel_apply(args) -> int:
    mode, prepared, user_store = _prepare_channel(args)
    with prepared:
        _print_prepared(prepared, mode, limit=args.limit)
        journal = _transaction_journal(args, prepared.localization.target)
        recovery = journal.inspect(
            prepared.localization.target,
            resolve_safe=args.confirm,
        )
        print(f"recovery : {recovery.status} — {recovery.message}")
        if recovery.needs_attention:
            print("refusing to write while recovery needs attention", file=sys.stderr)
            return EXIT_INVALID
        if args.expect_plan:
            expected = InjectionPlan.load(args.expect_plan)
            if expected.plan_id != prepared.plan.plan_id:
                print(
                    "refusing to write: current operation does not match --expect-plan",
                    file=sys.stderr,
                )
                return EXIT_INVALID
            print(f"expected : {args.expect_plan} (identity matched)")
        if args.plan_out:
            prepared.plan.save(args.plan_out)
            print(f"plan file: {args.plan_out}")
        if prepared.plan.errors:
            print("refusing to write an invalid plan", file=sys.stderr)
            return EXIT_INVALID
        if (
            not prepared.plan.updated
            and not prepared.plan.added
            and not prepared.plan.removed
        ):
            print("nothing to write; the channel already matches the prepared values")
            return EXIT_OK
        if not args.confirm:
            print("refusing to write without --confirm", file=sys.stderr)
            return EXIT_REFUSED

        # Revalidate and atomically normalize the irreplaceable user store
        # immediately before the replaceable game override is committed.
        if not args.no_user_edits and user_store.path.is_file():
            user_store.save(user_store.load())

        target = prepared.localization.target
        backup_dir = _channel_backup_dir(args, target)
        before = set(_channel_backups(backup_dir, target))
        result = prepared.commit(
            confirmed=True,
            backup_dir=backup_dir,
            journal=journal,
        )
        created = [path for path in _channel_backups(backup_dir, target) if path not in before]
        print(f"applied  : {result.summary()}")
        print(f"backup   : {created[0] if created else 'not created (no changes)'}")
        print(f"verified : {result.desired_sha256}")
        print(f"status   : {result.transaction_status}")
        return EXIT_OK


def _show_user_store(store: UserEditStore, session: EditSession | None = None) -> None:
    values = session.values if session is not None else store.load()
    print(f"user.ini : {store.path}")
    print(f"overrides: {len(values)}")
    if session is not None:
        print(f"undo     : {session.cursor}")
        print(f"redo     : {len(session.commands) - session.cursor}")
        if not session.history_recovered:
            print("history  : reset because it did not match user.ini", file=sys.stderr)


def cmd_user_list(args) -> int:
    store = _user_store(args, args.channel)
    session = EditSession(store)
    _show_user_store(store, session)
    for key, value in sorted(session.values.items()):
        print(f"  {key}={value}")
    return EXIT_OK


def cmd_user_set(args) -> int:
    store = _user_store(args, args.channel)
    session = EditSession(store)
    before = session.values.get(args.key)
    print(f"user.ini : {store.path}")
    print(f"key      : {args.key}")
    print(f"before   : {before if before is not None else '(not set)'}")
    print(f"after    : {args.value}")
    if not args.confirm:
        print("refusing to write without --confirm", file=sys.stderr)
        return EXIT_REFUSED
    session.execute(EditCommand.set(session.values, args.key, args.value))
    print("saved    : 1 user override command")
    return EXIT_OK


def cmd_user_remove(args) -> int:
    store = _user_store(args, args.channel)
    session = EditSession(store)
    command = EditCommand.remove(session.values, args.key)
    print(f"user.ini : {store.path}")
    print(f"remove   : {args.key}")
    if not args.confirm:
        print("refusing to write without --confirm", file=sys.stderr)
        return EXIT_REFUSED
    session.execute(command, allow_empty=True)
    print("saved    : override removed")
    return EXIT_OK


def cmd_user_import(args) -> int:
    store = _user_store(args, args.channel)
    session = EditSession(store)
    choice = ConflictChoice(args.on_conflict)
    incoming = load_language_pack(args.file)
    plan = plan_import(session.values, incoming, choice=choice)
    print(f"source   : {args.file}")
    print(f"user.ini : {store.path}")
    print(f"plan     : {plan.summary()}")
    for key in plan.conflicts[: args.limit]:
        print(
            f"  conflict {key}: kept existing"
            if choice is ConflictChoice.KEEP
            else f"  conflict {key}: {'use incoming' if choice is ConflictChoice.INCOMING else 'unresolved'}"
        )
    if plan.conflicts and choice is ConflictChoice.ERROR:
        print("choose --on-conflict keep or incoming before importing", file=sys.stderr)
        return EXIT_INVALID
    if not plan.changes:
        print("nothing to write")
        return EXIT_OK
    if not args.confirm:
        print("nothing was written; re-run with --confirm", file=sys.stderr)
        return EXIT_REFUSED
    session.import_plan(plan)
    print("saved    : import is one undoable command")
    return EXIT_OK


def cmd_user_export(args) -> int:
    store = _user_store(args, args.channel)
    values = store.load()
    print(f"user.ini : {store.path}")
    print(f"export   : {args.out}")
    print(f"overrides: {len(values)}")
    if not args.confirm:
        print("refusing to write without --confirm", file=sys.stderr)
        return EXIT_REFUSED
    store.export(args.out)
    print("exported : complete")
    return EXIT_OK


def cmd_user_undo(args) -> int:
    store = _user_store(args, args.channel)
    session = EditSession(store)
    if not session.can_undo:
        raise ValueError("nothing to undo")
    command = session.commands[session.cursor - 1]
    print(f"user.ini : {store.path}")
    print(f"undo     : {command.label}")
    if not args.confirm:
        print("refusing to write without --confirm", file=sys.stderr)
        return EXIT_REFUSED
    session.undo()
    print("undo     : complete")
    return EXIT_OK


def cmd_user_redo(args) -> int:
    store = _user_store(args, args.channel)
    session = EditSession(store)
    if not session.can_redo:
        raise ValueError("nothing to redo")
    command = session.commands[session.cursor]
    print(f"user.ini : {store.path}")
    print(f"redo     : {command.label}")
    if not args.confirm:
        print("refusing to write without --confirm", file=sys.stderr)
        return EXIT_REFUSED
    session.redo()
    print("redo     : complete")
    return EXIT_OK


def cmd_channel_rollback(args) -> int:
    game = _resolve_install(args.install)
    target = game.localization(args.language)
    journal = _transaction_journal(args, target)
    recovery = journal.inspect(target, resolve_safe=args.confirm)
    backup_dir = _channel_backup_dir(args, target)
    backups = _channel_backups(backup_dir, target)

    if args.list:
        if not backups:
            print(f"no backups found in {backup_dir}")
        else:
            for path in backups:
                print(path)
        return EXIT_OK

    if args.backup is None:
        if not backups:
            raise ValueError(f"no backups found in {backup_dir}")
        selected = backups[0]
    else:
        selected = (
            args.backup if args.backup.is_absolute() else backup_dir / args.backup
        ).resolve()
        if selected.parent != backup_dir.resolve():
            raise ValueError("--backup must name a file directly inside --backup-dir")
        if selected not in [path.resolve() for path in backups]:
            raise ValueError(f"{selected} is not a recognized backup for {target.name}")

    print(f"game     : {game.label}")
    print(f"language : {args.language}")
    print(f"target   : {target}")
    print(f"backup   : {selected}")
    print(f"bytes    : {selected.stat().st_size:,}")
    print(f"recovery : {recovery.status} — {recovery.message}")
    if recovery.needs_attention:
        print("refusing rollback while recovery needs attention", file=sys.stderr)
        return EXIT_INVALID
    if not args.confirm:
        print("refusing to restore without --confirm", file=sys.stderr)
        return EXIT_REFUSED

    before = fingerprint(target)
    selected_fingerprint = fingerprint(selected)
    selected_data = selected.read_bytes()
    after_sha256 = bytes_sha256(selected_data)
    rollback_id = bytes_sha256(
        f"rollback\0{target.resolve()}\0{before.sha256}\0{after_sha256}".encode(
            "utf-8"
        )
    )
    journal.begin(
        operation="rollback",
        plan_id=rollback_id,
        target=target,
        before=before,
        after_sha256=after_sha256,
    )
    recovery_backup = backup(target, backup_dir) if target.is_file() else None
    if recovery_backup is not None:
        journal.record_backup(recovery_backup)
    if fingerprint(target) != before:
        raise RuntimeError("target changed while preparing rollback; nothing was restored")
    restore(
        selected,
        target,
        expected_backup_fingerprint=selected_fingerprint,
        expected_target_fingerprint=before,
    )
    journal.record_replaced()
    final = fingerprint(target)
    if final.sha256 != after_sha256:
        raise RuntimeError("rollback verification fingerprint mismatch")
    journal.complete(final=final)
    print("rollback : complete")
    print(f"recovery backup: {recovery_backup or 'none (target did not exist)'}")
    print(f"verified : {after_sha256}")
    return EXIT_OK


def cmd_channel_diagnostics(args) -> int:
    game = _resolve_install(args.install)
    target = game.localization(args.language)
    journal = _transaction_journal(args, target)
    recovery = journal.inspect(target)
    current = fingerprint(target)
    print(f"game      : {game.label}")
    print(f"language  : {args.language}")
    print(f"target    : {target}")
    print(f"target id : {current.sha256 or 'missing override'}")
    print(f"recovery  : {recovery.status} — {recovery.message}")
    if recovery.plan_id:
        print(f"pending id: {recovery.plan_id}")
    if recovery.backup:
        print(f"backup    : {recovery.backup}")
    last = journal.last_operation()
    if last is None:
        print("last op   : none")
    else:
        print(f"last op   : {last.get('operation')} / {last.get('stage')}")
        print(f"last id   : {last.get('plan_id')}")
        print(f"last backup: {last.get('backup') or 'none'}")
        final = last.get("final")
        if isinstance(final, dict):
            print(f"last result: {final.get('sha256')}")
    return EXIT_INVALID if recovery.needs_attention else EXIT_OK


def cmd_stock(args) -> int:
    """Pull the pristine global.ini out of the game archive.

    Read-only against the install: the archive is opened `rb` and only the
    chosen entry is written, to a path the user names.
    """
    if args.list_languages:
        try:
            with P4KArchive(args.archive, entry_filter=is_localization_entry) as archive:
                for language in archive.languages():
                    print(language)
        except (P4KError, KeyError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK

    args.out.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{args.out.name}.", suffix=".tmp", dir=args.out.parent
    )
    temporary_path = Path(temporary_name)
    integrity_warnings: list[str] = []
    try:
        with os.fdopen(descriptor, "wb") as output:
            written = stream_stock_localization(
                args.archive,
                output.write,
                args.language,
                integrity_warning=integrity_warnings.append,
            )
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, args.out)
    except (P4KError, KeyError, OSError) as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"extracted stock {args.language} global.ini")
    print(f"  from    : {args.archive}")
    print(f"  bytes   : {written:,}")
    print(f"  written : {args.out}")
    for warning in integrity_warnings:
        print(f"  integrity warning: {warning}", file=sys.stderr)
    return EXIT_OK


def cmd_datacore(args) -> int:
    """Report what a DataCore holds. Read-only."""
    try:
        core = datacore.load(args.dcb)
    except datacore.DataCoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    for name, value in core.summary().items():
        print(f"{name:12}: {value:,}" if isinstance(value, int) else f"{name:12}: {value}")

    if args.structs:
        print()
        for name in core.find_structs(args.structs):
            print(f"  {name:52} {len(core.records_of(name)):>7,} records")

    if args.mission_facts:
        result = dataforge.extract_mission_facts(core)
        report = result.capability
        print()
        print("mission facts")
        print(f"  status   : {report.status.value}")
        print(f"  records  : {report.records_examined:,}")
        print(f"  facts    : {report.facts_emitted:,}")
        print(f"  titled   : {sum(bool(fact.title_keys) for fact in result.facts):,}")
        print(f"  reputation: {sum(bool(fact.reputation) for fact in result.facts):,} facts")
        print(
            f"  blueprints: {sum(bool(fact.blueprint_pools) for fact in result.facts):,} facts, "
            f"{sum(len(pool.items) for fact in result.facts for pool in fact.blueprint_pools):,} expanded items"
        )
        print(f"  item rewards: {sum(bool(fact.item_rewards) for fact in result.facts):,} facts")
        print(f"  evidence : {sum(len(fact.evidence) for fact in result.facts):,} links")
        severities = {severity: 0 for severity in dataforge.Severity}
        for diagnostic in report.diagnostics:
            severities[diagnostic.severity] += 1
        print(
            "  diagnostics: "
            + ", ".join(f"{severity.value}={severities[severity]:,}" for severity in dataforge.Severity)
        )
        for diagnostic in report.diagnostics[: args.diagnostic_limit]:
            location = diagnostic.record_path or diagnostic.record_id or "provider"
            if diagnostic.field_path:
                location += f":{diagnostic.field_path}"
            print(f"    [{diagnostic.severity.value}] {diagnostic.code} at {location}: {diagnostic.message}")
        if report.status is dataforge.CapabilityStatus.UNAVAILABLE:
            return EXIT_INVALID

    return EXIT_OK


def cmd_scmdb(args) -> int:
    """Apply or compare a SCMDB export against a cache.

    Reads a file you exported from https://scmdb.net/ yourself -- this never
    contacts the network, and SCMDB's robots.txt excludes automated access to
    its data endpoints.
    """
    try:
        export = scmdb.load(args.export)
    except scmdb.ScmdbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID

    contracts = cache.load(args.cache)
    print(f"export : {export.summary()}")

    if args.compare:
        # Read-only: report disagreements, change nothing.
        result = merge.compare_pools(contracts, export)
        print(f"compare: {result.summary()}")
        for disagreement in result.disagreements[: args.limit]:
            print(f"  {disagreement.summary()}")
            for item in disagreement.only_ours[:5]:
                print(f"    only local : {item}")
            for item in disagreement.only_theirs[:5]:
                print(f"    only SCMDB : {item}")
        print()
        print("nothing was written; drop --compare to apply")
        return EXIT_OK

    owned = merge.apply_ownership(contracts, export)
    if owned.items_marked:
        print(f"owned  : {owned.summary()}")
        if owned.unmatched:
            print(f"         {len(owned.unmatched):,} owned items match no contract pool")

    filled = merge.apply_pools(contracts, export, overwrite=args.overwrite)
    if filled:
        print(f"pools  : {filled:,} contracts updated")

    cache.save(contracts, args.out or args.cache, source=f"scmdb:{export.source_file}")
    print(f"written: {args.out or args.cache}")
    return EXIT_OK


def cmd_restore(args) -> int:
    restore(args.backup, args.target)
    print(f"restored {args.target} from {args.backup}")
    return EXIT_OK


def cmd_profiles(args) -> int:
    for name, path in builtin_profiles().items():
        print(f"{name:12} {Profile.load(path).description}")
    return EXIT_OK


def cmd_inspect(args) -> int:
    for field, value in cache.describe(args.cache).items():
        print(f"{field:14}: {value}")
    contracts = cache.load(args.cache)
    for capability in contracts.capabilities:
        print(
            f"provider      : {capability.provider} v{capability.version} "
            f"[{capability.status.value}]"
        )
        print(f"  build       : {capability.build_version}")
        print(f"  facts       : {capability.facts_seen:,}")
        print(f"  enhanced    : {capability.contracts_enhanced:,}")
        print(f"  evidence    : {capability.evidence_links:,}")
        print(
            f"  coverage    : {capability.matched_facts:,}/"
            f"{capability.reward_facts:,} reward facts matched; "
            f"{capability.unmatched_facts:,} unmatched"
        )
        if capability.diagnostic_counts:
            print(
                "  diagnostic classes: "
                + ", ".join(
                    f"{category}={count:,}"
                    for category, count in capability.diagnostic_counts
                )
            )
        if capability.unmatched_reason_counts:
            print(
                "  unmatched reasons: "
                + ", ".join(
                    f"{reason}={count:,}"
                    for reason, count in capability.unmatched_reason_counts
                )
            )
        for diagnostic in capability.diagnostics[:3]:
            print(f"  diagnostic  : {diagnostic}")
        for sample in capability.unmatched_samples[:3]:
            print(f"  unmatched   : {sample}")
    return EXIT_OK


def _blueprint_context(args):
    contracts = cache.load(args.cache)
    catalog = build_catalog(contracts)
    channel = args.channel or _cache_channel(args.cache)
    if not channel:
        raise ValueError("--channel is required when the cache is not game-scoped")
    store = OwnershipStore(
        channel,
        root=args.data_root,
        link_live_hotfix=args.link_live_hotfix,
    )
    return catalog, store, store.load()


def _print_blueprint_rows(rows, *, as_json: bool, limit: int) -> None:
    selected = rows[: max(0, limit)] if limit else rows
    if as_json:
        print(json.dumps([
            {
                "blueprint_id": row.entry.blueprint_id,
                "name": row.entry.name,
                "category": row.entry.category,
                "owned": row.owned,
                "acquired_at": row.acquired_at,
                "acquisition_sources": list(row.acquisition_sources),
                "reward_sources": [
                    {
                        "contract_id": source.contract_id,
                        "org": source.org,
                        "family": source.family,
                        "pool": source.pool,
                        "chance": source.chance,
                        "gates": list(source.gates),
                    }
                    for source in sorted(row.entry.reward_sources)
                ],
            }
            for row in selected
        ], ensure_ascii=False, indent=2))
        return
    for row in selected:
        status = "owned" if row.owned else "unowned"
        sources = ", ".join(row.acquisition_sources) or "-"
        print(f"{status:7} {row.entry.category:18} {row.entry.name} [{sources}]")


def cmd_blueprints_list(args) -> int:
    catalog, _store, state = _blueprint_context(args)
    rows = query_blueprints(
        catalog,
        state,
        BlueprintQuery(
            search=args.search or "",
            ownership=OwnershipFilter(args.ownership),
            reward_source=args.reward_source or "",
            category=args.category or "",
            acquisition_source=args.acquisition_source or "",
        ),
    )
    print(f"catalog : {len(catalog.entries):,} blueprints")
    print(f"scope   : {state.scope}")
    print(f"results : {len(rows):,}")
    _print_blueprint_rows(rows, as_json=args.json, limit=args.limit)
    return EXIT_OK


def cmd_blueprints_scan(args) -> int:
    catalog, store, state = _blueprint_context(args)
    paths = [Path(path) for path in args.log]
    for root in args.install:
        paths.extend(discover_log_files(root))
    if not paths:
        raise ValueError("select at least one --log or --install to scan")
    result = scan_logs(paths, catalog, state, full_rescan=args.full)
    print(f"scope        : {state.scope}")
    print(f"files        : {result.files_read:,}/{result.files_seen:,} read")
    print(f"bytes        : {result.bytes_read:,}")
    print(f"events       : {result.events_seen:,}")
    print(f"new evidence : {result.acquisitions_added:,}")
    print(f"unresolved   : {result.unresolved_added:,} new, {result.unresolved_reconciled:,} reconciled")
    print(f"unmatched    : {len(result.unmatched_names):,}")
    for item in result.unmatched_names[: max(0, args.limit)]:
        print(f"  unmatched: {item}")
    for item in result.diagnostics[: max(0, args.limit)]:
        print(f"  {item.code}: {item.source_name}: {item.message}")
    if not args.confirm:
        print("nothing was written; repeat with --confirm to save acquisitions and watermarks")
        return EXIT_REFUSED
    if result.state == state:
        print("unchanged     : no ownership or watermark write was needed")
        return EXIT_OK
    store.save(result.state)
    print(f"written      : {store.path}")
    return EXIT_OK


def cmd_blueprints_import(args) -> int:
    catalog, store, state = _blueprint_context(args)
    plan = plan_ownership_import(args.file, catalog, state)
    print(f"source        : {plan.source_name}")
    print(f"add           : {plan.additions:,}")
    print(f"already owned : {len(plan.already_owned):,}")
    print(f"unmatched     : {len(plan.unmatched_names):,}")
    for item in plan.unmatched_names[: max(0, args.limit)]:
        print(f"  unmatched: {item}")
    if not args.confirm:
        print("nothing was written; repeat with --confirm to import matched ownership")
        return EXIT_REFUSED
    store.save(apply_ownership_import(plan, state))
    print(f"written       : {store.path}")
    return EXIT_OK


def cmd_blueprints_export(args) -> int:
    catalog, store, state = _blueprint_context(args)
    if not args.confirm:
        print(f"would export {len(state.records):,} owned blueprints to {args.out}")
        print("nothing was written; repeat with --confirm")
        return EXIT_REFUSED
    payload = (
        export_ownership_json(state, catalog)
        if args.out.suffix.casefold() == ".json"
        else export_ownership_csv(state, catalog)
    )
    write_ownership_export(args.out, payload, store_path=store.path)
    print(f"exported : {len(state.records):,}")
    print(f"written  : {args.out}")
    return EXIT_OK


def cmd_blueprints_diagnostics(args) -> int:
    catalog, _store, state = _blueprint_context(args)
    known = set(catalog.by_id)
    stale = sorted(set(state.records) - known)
    fallback = sum(entry.identity_fallback for entry in catalog.entries)
    acquisitions = sum(len(record.acquisitions) for record in state.records.values())
    print(f"scope               : {state.scope}")
    print(f"catalog             : {len(catalog.entries):,}")
    print(f"name-fallback ids   : {fallback:,}")
    print(f"owned               : {len(state.records):,}")
    print(f"acquisition evidence: {acquisitions:,}")
    print(f"unresolved evidence : {len(state.unresolved):,}")
    print(f"scan cursors        : {len(state.cursors):,}")
    print(f"not in this build   : {len(stale):,}")
    print("diagnostics contain counts only; player item names and evidence are omitted")
    return EXIT_OK


def cmd_blueprints_unresolved(args) -> int:
    catalog, _store, state = _blueprint_context(args)
    print(f"scope      : {state.scope}")
    print(f"unresolved : {len(state.unresolved):,}")
    for item in state.unresolved[: max(0, args.limit)]:
        candidates = catalog.resolve_name_candidates(item.name)
        print(
            f"{item.acquisition.acquisition_id[:16]}  {item.reason:9}  "
            f"{item.name}"
        )
        for blueprint_id in candidates:
            print(f"  candidate: {blueprint_id}  {catalog.by_id[blueprint_id].name}")
    print("this explicit command may display player-owned item names")
    return EXIT_OK


def cmd_blueprints_resolve(args) -> int:
    catalog, store, state = _blueprint_context(args)
    plan = plan_ownership_resolution(
        state, catalog, args.acquisition, args.blueprint_id
    )
    print(f"acquisition : {plan.unresolved.acquisition.acquisition_id}")
    print(f"logged name : {plan.unresolved.name}")
    print(f"blueprint   : {plan.blueprint_id} ({plan.blueprint_name})")
    if not args.confirm:
        print("nothing was written; repeat with --confirm to apply this exact resolution")
        return EXIT_REFUSED
    store.save(apply_ownership_resolution(plan, state))
    print(f"written     : {store.path}")
    return EXIT_OK


def cmd_blueprints_recover(args) -> int:
    channel = args.channel or _cache_channel(args.cache)
    if not channel:
        raise ValueError("--channel is required when the cache is not game-scoped")
    store = OwnershipStore(
        channel,
        root=args.data_root,
        link_live_hotfix=args.link_live_hotfix,
    )
    backup_state = store.load_backup()
    acquisitions = sum(
        len(record.acquisitions) for record in backup_state.records.values()
    )
    print(f"scope               : {backup_state.scope}")
    print(f"backup revision     : {backup_state.revision}")
    print(f"owned               : {len(backup_state.records):,}")
    print(f"acquisition evidence: {acquisitions:,}")
    print(f"unresolved evidence : {len(backup_state.unresolved):,}")
    if not args.confirm:
        print("nothing was written; repeat with --confirm to restore this backup")
        return EXIT_REFUSED
    recovered = store.recover()
    print(f"recovered revision  : {recovered.revision}")
    print(f"written             : {store.path}")
    return EXIT_OK


def cmd_channels_list(args) -> int:
    games = installs.find_installs(roots=args.root or None)
    if not games:
        print("no supported Star Citizen channels were found")
        return EXIT_ERROR
    for game in games:
        try:
            languages = game.languages()
            language_text = ", ".join(languages) if languages else "none"
        except P4KError as exc:
            language_text = f"unavailable ({exc})"
        print(f"{game.channel}")
        print(f"  install   : {game.root}")
        print(f"  version   : {game.version or 'unknown'}")
        print(f"  languages : {language_text}")
        print(f"  configured: {game.configured_language or 'not set'}")
    return EXIT_OK


def cmd_languages_list(args) -> int:
    game = _resolve_install(args.install)
    available = game.languages()
    print(f"channel   : {game.channel}")
    print(f"configured: {game.configured_language or 'not set'}")
    for language in available:
        suffix = " (configured)" if language == game.configured_language else ""
        print(f"  {language}{suffix}")
    return EXIT_OK if available else EXIT_INVALID


def cmd_languages_import(args) -> int:
    game = _resolve_install(args.install)
    language = installs.normalize_language(args.language)
    available = game.languages()
    if language not in available:
        raise ValueError(
            f"language {language!r} is not installed in {game.channel}; "
            f"available: {', '.join(available) or 'none'}"
        )
    incoming = load_user_ini(args.file)
    store = LanguagePackStore(game.channel, language, args.data_root)
    current = store.load()
    added = sorted(set(incoming) - set(current))
    changed = sorted(key for key in incoming.keys() & current.keys() if incoming[key] != current[key])
    unchanged = sorted(key for key in incoming.keys() & current.keys() if incoming[key] == current[key])
    print(f"channel  : {game.channel}")
    print(f"language : {language}")
    print(f"source   : {args.file}")
    print(f"plan     : add {len(added):,}, change {len(changed):,}, unchanged {len(unchanged):,}")
    if not args.confirm:
        print("nothing was written; repeat with --confirm to store this local language pack")
        return EXIT_REFUSED
    store.save(incoming)
    print(f"written  : {store.path}")
    print("network  : no remote translation source was contacted")
    return EXIT_OK


def cmd_settings_show(args) -> int:
    preferences = PreferencesStore(args.data_root or data_dir()).load()
    print(json.dumps(preferences, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_settings_export(args) -> int:
    root = args.data_root or data_dir()
    plan = plan_settings_export(root)
    counts: dict[str, int] = {}
    for entry in plan.entries:
        counts[entry.kind] = counts.get(entry.kind, 0) + 1
    print(f"data root : {Path(root).resolve()}")
    print(f"archive   : {args.out}")
    print(f"files     : {len(plan.entries):,}")
    for kind, count in sorted(counts.items()):
        print(f"  {kind}: {count:,}")
    print("excluded  : caches, histories, ownership, logs, backups, game strings")
    if not args.confirm:
        print("nothing was written; repeat with --confirm to create this settings archive")
        return EXIT_REFUSED
    write_settings_archive(plan, args.out, overwrite=args.overwrite)
    print(f"written   : {args.out}")
    return EXIT_OK


def cmd_settings_import(args) -> int:
    root = args.data_root or data_dir()
    plan = plan_settings_import(args.file, root)
    outcomes = {name: 0 for name in ("add", "change", "unchanged")}
    for item in plan.items:
        outcomes[item.outcome] += 1
    print(f"archive   : {args.file}")
    print(f"data root : {Path(root).resolve()}")
    print(
        f"plan      : add {outcomes['add']:,}, change {outcomes['change']:,}, "
        f"unchanged {outcomes['unchanged']:,}"
    )
    for item in plan.items:
        scope = (
            f"{item.channel}/{item.language}" if item.channel and item.language else "global"
        )
        print(f"  {item.outcome:9} {item.kind:14} {scope}")
    if not args.confirm:
        print("nothing was written; repeat with --confirm to restore this exact plan")
        return EXIT_REFUSED
    if outcomes["change"] and not args.replace_existing:
        print(
            "refusing to replace existing settings without --replace-existing",
            file=sys.stderr,
        )
        return EXIT_INVALID
    apply_settings_import(plan, replace_existing=args.replace_existing)
    print("restore   : complete")
    return EXIT_OK


def cmd_settings_recover(args) -> int:
    root = Path(args.data_root or data_dir()).resolve()
    status = settings_recovery_status(root)
    if status is None:
        raise ValueError("no interrupted settings restore requires recovery")
    print(f"data root : {root}")
    print(f"recovery  : {status}")
    if not args.confirm:
        print("nothing was written; repeat with --confirm to recover this restore")
        return EXIT_REFUSED
    result = recover_settings_restore(root)
    print(f"recovery  : {result}")
    return EXIT_OK


def _diagnostic_installs(args):
    games = []
    for path in args.install or ():
        game = installs.identify(path)
        if game is None:
            raise ValueError(f"no supported install containing Data.p4k found at {path}")
        games.append(game)
    if not games:
        games = installs.find_installs(roots=args.root or None)
    return tuple({game.root.resolve(): game for game in games}.values())


def cmd_diagnostics_preview(args) -> int:
    report = build_diagnostics(
        _diagnostic_installs(args), root=args.data_root or data_dir()
    )
    print(render_diagnostics(report).decode("utf-8"), end="")
    print("nothing was written; diagnostics omit paths, values, logs, ownership, and game strings")
    return EXIT_OK


def cmd_diagnostics_export(args) -> int:
    report = build_diagnostics(
        _diagnostic_installs(args), root=args.data_root or data_dir()
    )
    print(f"installs : {len(report['installs']):,}")
    print(f"scopes   : {len(report['portable_data']['scopes']):,}")
    print("redacted : paths, usernames, user values, logs, ownership, game strings")
    if not args.confirm:
        print("nothing was written; repeat with --confirm to export this redacted report")
        return EXIT_REFUSED
    write_diagnostics(report, args.out, overwrite=args.overwrite)
    print(f"written  : {args.out}")
    return EXIT_OK


def cmd_fallbacks_template(args) -> int:
    contracts = cache.load(args.cache)
    document = template_from_contracts(
        contracts,
        game_version=args.game_version or _cache_game_version(args.cache),
        language=args.language,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    document.save(args.out)
    print(f"unresolved missions : {len(document.unresolved)}")
    print(f"authorable keys     : {len(document.values)}")
    print(f"written             : {args.out}")
    print("no text was generated; fill values yourself, then run `fallbacks validate`")
    return EXIT_OK


def cmd_fallbacks_validate(args) -> int:
    document = FallbackDocument.load(args.file)
    if args.install is not None:
        game = _resolve_install(args.install)
        document.validate_context(
            game_version=game.version,
            language=args.language or document.language,
        )
    elif args.language is not None and args.language.casefold() != document.language.casefold():
        raise ValueError(
            f"fallback language {document.language!r} does not match {args.language!r}"
        )
    print(f"schema              : {SCHEMA_VERSION}")
    print(f"build               : {document.game_version or 'not pinned'}")
    print(f"language            : {document.language}")
    print(f"unresolved missions : {len(document.unresolved)}")
    print(f"authored keys       : {len(document.authored_values)}")
    print("contextual DataForge validation occurs during `import --fallbacks`")
    return EXIT_OK


# --- wiring ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="starcompanion",
        description="Build a customised Star Citizen global.ini from contract data.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="read contracts from your game (or a contract list)")
    p.add_argument("--install", type=Path, help="game folder; found automatically if omitted")
    p.add_argument(
        "--contracts", type=Path,
        help="optional community contract list for rendered reward annotations",
    )
    p.add_argument(
        "--language", default="english",
        help="localization language to read from Data.p4k (default: english)",
    )
    p.add_argument("--out", type=Path, default=Path("cache.json"))
    p.add_argument(
        "--fallbacks",
        type=Path,
        help="explicit user-authored missing-key fallback document",
    )
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("render", help="turn a cache into localization values")
    p.add_argument("--cache", type=Path, default=Path("cache.json"))
    p.add_argument("--profile", help="built-in profile name or path to a profile JSON")
    p.add_argument("--out", type=Path, default=Path("rendered.json"))
    p.add_argument(
        "--channel",
        help="user.ini channel; inferred from a game cache when omitted",
    )
    p.add_argument("--language", default="english")
    p.add_argument("--data-root", type=Path, help="override the per-user data root")
    p.add_argument(
        "--no-user-edits",
        action="store_true",
        help="render without the selected channel's user.ini layer",
    )
    p.add_argument(
        "--language-overlay",
        type=Path,
        help="optional local language overlay INI",
    )
    p.add_argument(
        "--no-language-pack",
        action="store_true",
        help="render without the persisted channel/language local pack",
    )
    p.add_argument(
        "--import-source",
        type=Path,
        action="append",
        help="configured local INI layer; repeat in desired precedence order",
    )
    p.add_argument(
        "--provenance-out",
        type=Path,
        help="optional JSON sidecar tracing every rendered key to local evidence",
    )
    p.add_argument(
        "--sources-out",
        type=Path,
        help="write complete source precedence/provenance report JSON",
    )
    p.add_argument(
        "--conflicts-out",
        type=Path,
        help="write only keys whose source layers disagree",
    )
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("plan", help="preview changes without writing")
    p.add_argument("--rendered", type=Path, default=Path("rendered.json"))
    p.add_argument("--target", type=Path, required=True, help="global.ini to modify")
    p.add_argument("--fallbacks", type=Path, help="authorize additions from this fallback document")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", help="write changes (requires --confirm)")
    p.add_argument("--rendered", type=Path, default=Path("rendered.json"))
    p.add_argument("--target", type=Path, required=True)
    p.add_argument("--profile", help="supplies the default merge mode")
    p.add_argument("--mode", choices=[m.value for m in MergeMode])
    p.add_argument("--stock", type=Path, help="pristine global.ini, for overwrite mode")
    p.add_argument("--backup-dir", type=Path)
    p.add_argument("--fallbacks", type=Path, help="authorize additions from this fallback document")
    p.add_argument("--confirm", action="store_true", help="required to write anything")
    p.add_argument(
        "--allow-game-folder",
        action="store_true",
        help="permit writing inside a detected Star Citizen install",
    )
    p.set_defaults(func=cmd_apply)

    channel = sub.add_parser(
        "channel",
        help="prepare, preview, apply, or roll back an installed game channel",
    )
    channel_sub = channel.add_subparsers(dest="channel_command", required=True)

    def add_install(selection) -> None:
        selection.add_argument(
            "--install",
            type=Path,
            help="channel folder containing Data.p4k; found automatically if omitted",
        )
        selection.add_argument("--language", default="english")
        selection.add_argument("--backup-dir", type=Path)
        selection.add_argument("--data-root", type=Path)

    def add_preparation(selection) -> None:
        add_install(selection)
        selection.add_argument("--rendered", type=Path, default=Path("rendered.json"))
        selection.add_argument(
            "--mode", choices=[mode.value for mode in MergeMode], default="merge"
        )
        selection.add_argument("--limit", type=int, default=10)
        selection.add_argument(
            "--fallbacks",
            type=Path,
            help="authorize additions from this fallback document",
        )
        selection.add_argument(
            "--no-user-edits",
            action="store_true",
            help="do not overlay the channel/language user.ini",
        )
        selection.add_argument(
            "--sources",
            type=Path,
            help="source report from render --sources-out to embed winner/conflict metadata",
        )
        selection.add_argument(
            "--remove-key",
            action="append",
            help="remove a user-added key authorized by --fallbacks; repeatable",
        )
        selection.add_argument(
            "--plan-out",
            type=Path,
            help="write the versioned operation plan shown by this command",
        )

    p = channel_sub.add_parser(
        "preview", help="prepare the install and show changes without writing"
    )
    add_preparation(p)
    p.set_defaults(func=cmd_channel_preview)

    p = channel_sub.add_parser(
        "apply", help="prepare, show, and atomically apply changes"
    )
    add_preparation(p)
    p.add_argument("--confirm", action="store_true", help="required to write anything")
    p.add_argument(
        "--expect-plan",
        type=Path,
        help="apply only if the fresh plan identity matches this reviewed plan",
    )
    p.set_defaults(func=cmd_channel_apply)

    p = channel_sub.add_parser(
        "rollback", help="restore the latest channel backup atomically"
    )
    add_install(p)
    p.add_argument("--backup", type=Path, help="specific scoped backup; latest by default")
    p.add_argument("--list", action="store_true", help="list available backups and exit")
    p.add_argument("--confirm", action="store_true", help="required to restore anything")
    p.set_defaults(func=cmd_channel_rollback)

    p = channel_sub.add_parser(
        "diagnostics", help="show target identity and apply/rollback recovery state"
    )
    add_install(p)
    p.set_defaults(func=cmd_channel_diagnostics)

    p = sub.add_parser("stock", help="extract the pristine global.ini from Data.p4k")
    p.add_argument("--archive", type=Path, required=True, help="path to Data.p4k")
    p.add_argument("--language", default="english")
    p.add_argument("--out", type=Path, default=Path("stock-global.ini"))
    p.add_argument(
        "--list-languages", action="store_true", help="list languages and exit"
    )
    p.set_defaults(func=cmd_stock)

    p = sub.add_parser("datacore", help="inspect a DataCore (.dcb) database")
    p.add_argument("--dcb", type=Path, required=True, help="path to Game2.dcb")
    p.add_argument("--structs", help="list struct names containing this text")
    p.add_argument(
        "--mission-facts",
        action="store_true",
        help="run the C1 mission graph and print its build-drift capability report",
    )
    p.add_argument(
        "--diagnostic-limit",
        type=int,
        default=25,
        help="maximum mission diagnostics to print (default: 25)",
    )
    p.set_defaults(func=cmd_datacore)

    p = sub.add_parser("scmdb", help="apply or compare a SCMDB export you downloaded")
    p.add_argument("--export", type=Path, required=True, help="JSON exported from scmdb.net")
    p.add_argument("--cache", type=Path, default=Path("cache.json"))
    p.add_argument("--out", type=Path, help="defaults to updating --cache in place")
    p.add_argument(
        "--compare", action="store_true",
        help="report differences without changing anything",
    )
    p.add_argument(
        "--overwrite", action="store_true",
        help="prefer SCMDB pools over local ones (default: only fill gaps)",
    )
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_scmdb)

    p = sub.add_parser("restore", help="put a backup back")
    p.add_argument("--backup", type=Path, required=True)
    p.add_argument("--target", type=Path, required=True)
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("profiles", help="list built-in profiles")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("inspect", help="show what a cache holds")
    p.add_argument("--cache", type=Path, default=Path("cache.json"))
    p.set_defaults(func=cmd_inspect)

    blueprint_parser = sub.add_parser(
        "blueprints",
        help="query and manage local, channel-scoped blueprint ownership",
    )
    blueprint_sub = blueprint_parser.add_subparsers(
        dest="blueprint_command", required=True
    )

    def add_blueprint_scope(selection) -> None:
        selection.add_argument("--cache", type=Path, default=Path("cache.json"))
        selection.add_argument(
            "--channel", help="ownership channel; inferred from a game cache"
        )
        selection.add_argument("--data-root", type=Path)
        selection.add_argument(
            "--link-live-hotfix",
            action="store_true",
            help="use the explicit shared LIVE-HOTFIX ownership scope",
        )

    p = blueprint_sub.add_parser("list", help="search/filter the local catalog")
    add_blueprint_scope(p)
    p.add_argument("--search")
    p.add_argument(
        "--ownership",
        choices=[item.value for item in OwnershipFilter],
        default=OwnershipFilter.ALL.value,
    )
    p.add_argument("--reward-source")
    p.add_argument("--category")
    p.add_argument(
        "--acquisition-source", choices=("log", "import")
    )
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_blueprints_list)

    p = blueprint_sub.add_parser(
        "scan", help="preview or persist an incremental local log scan"
    )
    add_blueprint_scope(p)
    p.add_argument("--install", type=Path, action="append", default=[])
    p.add_argument("--log", type=Path, action="append", default=[])
    p.add_argument("--full", action="store_true", help="force a full rescan")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_blueprints_scan)

    p = blueprint_sub.add_parser(
        "import", help="preview or import CSV/SCMDB-shaped JSON ownership"
    )
    add_blueprint_scope(p)
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_blueprints_import)

    p = blueprint_sub.add_parser(
        "export", help="export owned blueprints as .json or .csv"
    )
    add_blueprint_scope(p)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_blueprints_export)

    p = blueprint_sub.add_parser(
        "diagnostics", help="show catalog identity and ownership health"
    )
    add_blueprint_scope(p)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_blueprints_diagnostics)

    p = blueprint_sub.add_parser(
        "unresolved",
        help="inspect retained ambiguous/no-match acquisition evidence",
    )
    add_blueprint_scope(p)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_blueprints_unresolved)

    p = blueprint_sub.add_parser(
        "resolve",
        help="preview or resolve one acquisition to an exact-name candidate",
    )
    add_blueprint_scope(p)
    p.add_argument(
        "--acquisition",
        required=True,
        help="unique acquisition hash prefix (at least 8 characters)",
    )
    p.add_argument("--blueprint-id", required=True)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_blueprints_resolve)

    p = blueprint_sub.add_parser(
        "recover",
        help="preview or restore the validated last-known-good ownership backup",
    )
    add_blueprint_scope(p)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_blueprints_recover)

    channels_parser = sub.add_parser(
        "channels", help="discover supported installed game channels and languages"
    )
    channels_sub = channels_parser.add_subparsers(dest="channels_command", required=True)
    p = channels_sub.add_parser("list", help="list every supported discovered install")
    p.add_argument(
        "--root",
        type=Path,
        action="append",
        help="search this StarCitizen parent or direct channel folder; repeatable",
    )
    p.set_defaults(func=cmd_channels_list)

    languages_parser = sub.add_parser(
        "languages", help="discover or import local-only language packs"
    )
    languages_sub = languages_parser.add_subparsers(
        dest="languages_command", required=True
    )
    p = languages_sub.add_parser("list", help="list languages installed in Data.p4k")
    p.add_argument("--install", type=Path)
    p.set_defaults(func=cmd_languages_list)

    p = languages_sub.add_parser(
        "import", help="preview/store a local INI language pack without network access"
    )
    p.add_argument("--install", type=Path)
    p.add_argument("--language", required=True)
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--data-root", type=Path)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_languages_import)

    settings_parser = sub.add_parser(
        "settings", help="inspect or safely move portable settings between machines"
    )
    settings_sub = settings_parser.add_subparsers(dest="settings_command", required=True)
    p = settings_sub.add_parser("show", help="show allowlisted portable preferences")
    p.add_argument("--data-root", type=Path)
    p.set_defaults(func=cmd_settings_show)

    p = settings_sub.add_parser("export", help="preview/create a portable settings ZIP")
    p.add_argument("--data-root", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_settings_export)

    p = settings_sub.add_parser("import", help="preview/restore a portable settings ZIP")
    p.add_argument("--data-root", type=Path)
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--replace-existing", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_settings_import)

    p = settings_sub.add_parser(
        "recover", help="preview/repair a crash-interrupted settings restore"
    )
    p.add_argument("--data-root", type=Path)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_settings_recover)

    diagnostics_parser = sub.add_parser(
        "diagnostics", help="preview or export a privacy-redacted support report"
    )
    diagnostics_sub = diagnostics_parser.add_subparsers(
        dest="diagnostics_command", required=True
    )

    def add_diagnostic_scope(selection) -> None:
        selection.add_argument("--install", type=Path, action="append")
        selection.add_argument("--root", type=Path, action="append")
        selection.add_argument("--data-root", type=Path)

    p = diagnostics_sub.add_parser("preview", help="print the redacted report")
    add_diagnostic_scope(p)
    p.set_defaults(func=cmd_diagnostics_preview)

    p = diagnostics_sub.add_parser("export", help="write the redacted report")
    add_diagnostic_scope(p)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_diagnostics_export)

    user_parser = sub.add_parser(
        "user",
        help="manage persistent per-channel/localization user.ini overrides",
    )
    user_sub = user_parser.add_subparsers(dest="user_command", required=True)

    def add_user_scope(selection) -> None:
        selection.add_argument("--channel", required=True)
        selection.add_argument("--language", default="english")
        selection.add_argument("--data-root", type=Path)

    p = user_sub.add_parser("list", help="show persistent overrides and history state")
    add_user_scope(p)
    p.set_defaults(func=cmd_user_list)

    p = user_sub.add_parser("set", help="set one persistent override")
    add_user_scope(p)
    p.add_argument("--key", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_user_set)

    p = user_sub.add_parser("remove", help="remove one persistent override")
    add_user_scope(p)
    p.add_argument("--key", required=True)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_user_remove)

    p = user_sub.add_parser(
        "import", help="preview or import another user.ini as one undoable command"
    )
    add_user_scope(p)
    p.add_argument("--file", type=Path, required=True)
    p.add_argument(
        "--on-conflict",
        choices=[choice.value for choice in ConflictChoice],
        default=ConflictChoice.ERROR.value,
    )
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_user_import)

    p = user_sub.add_parser("export", help="export the exact persistent user.ini")
    add_user_scope(p)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_user_export)

    p = user_sub.add_parser("undo", help="undo the last model command")
    add_user_scope(p)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_user_undo)

    p = user_sub.add_parser("redo", help="redo the next model command")
    add_user_scope(p)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_user_redo)

    fallback_parser = sub.add_parser(
        "fallbacks",
        help="author exact text for provider-referenced keys missing from CIG localization",
    )
    fallback_sub = fallback_parser.add_subparsers(
        dest="fallback_command",
        required=True,
    )
    p = fallback_sub.add_parser(
        "template",
        help="export an empty authoring template from structured cache gaps",
    )
    p.add_argument("--cache", type=Path, default=Path("cache.json"))
    p.add_argument("--out", type=Path, default=Path("fallbacks.json"))
    p.add_argument("--language", default="english")
    p.add_argument("--game-version", help="override a version not present in cache metadata")
    p.set_defaults(func=cmd_fallbacks_template)

    p = fallback_sub.add_parser(
        "validate",
        help="validate fallback syntax and optional build/language context",
    )
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--install", type=Path)
    p.add_argument("--language")
    p.set_defaults(func=cmd_fallbacks_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    from .offline import enforce_offline_from_environment

    enforce_offline_from_environment()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
