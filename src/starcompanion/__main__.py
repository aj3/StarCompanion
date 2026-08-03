"""Command line interface.

Anything that writes to the game file is gated: `plan` is the read-only preview,
`apply` refuses without `--confirm`, and a target inside a real game install
needs `--allow-game-folder` on top. Backups are taken by default.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cache
from .config import Profile, builtin_profiles, load_builtin
from .extract import datacore
from .extract.p4k import P4KArchive, P4KError
from .ini import LocalizationFile
from .inject import (
    MergeMode,
    UnconfirmedWriteError,
    ValidationFailedError,
    apply,
    looks_like_game_install,
    restore,
)
from .inject import plan as plan_injection
from . import install as installs
from .sources import contracts_ini, game_strings, merge, scmdb

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 3
EXIT_INVALID = 4

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


# --- commands ----------------------------------------------------------------


def cmd_import(args) -> int:
    """Read contracts. The game's own strings are the default source; a
    community contract list only adds reward values on top."""
    if args.contracts:
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
        contracts = game_strings.from_install(game)
        source = f"game:{game.channel}:{game.version or 'unknown'}"

    cache.save(contracts, args.out, source=source)

    print(f"imported {len(contracts.contracts)} contracts from {len(contracts.orgs)} orgs")
    print(f"  keys     : {sum(len(c.all_keys()) for c in contracts.contracts)}")
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
    args.out.write_text(json.dumps(result.values, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"rendered with profile '{profile.name}': {result.summary()}")
    for key, reason in result.skipped[:10]:
        print(f"  skipped {key}: {reason}", file=sys.stderr)
    print(f"  written : {args.out}")
    return EXIT_INVALID if result.skipped else EXIT_OK


def cmd_plan(args) -> int:
    target = LocalizationFile.load(args.target)
    result = plan_injection(target, _load_rendered(args.rendered))

    print(f"target : {args.target}")
    print(f"plan   : {result.summary()}")

    for key in result.skipped[:10]:
        print(f"  no such key in target: {key}", file=sys.stderr)
    for key, issue in result.errors[:10]:
        print(f"  {key}: {issue}", file=sys.stderr)

    print("\nnothing was written; re-run with `apply --confirm` to write")
    return EXIT_INVALID if result.errors else EXIT_OK


def cmd_apply(args) -> int:
    replacements = _load_rendered(args.rendered)

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
        )
    except UnconfirmedWriteError:
        preview = plan_injection(LocalizationFile.load(args.target), replacements)
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


def cmd_stock(args) -> int:
    """Pull the pristine global.ini out of the game archive.

    Read-only against the install: the archive is opened `rb` and only the
    chosen entry is written, to a path the user names.
    """
    try:
        with P4KArchive(args.archive) as archive:
            if args.list_languages:
                for language in archive.languages():
                    print(language)
                return EXIT_OK

            data = archive.read_localization(args.language)
    except (P4KError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)

    print(f"extracted stock {args.language} global.ini")
    print(f"  from    : {args.archive}")
    print(f"  bytes   : {len(data):,}")
    print(f"  written : {args.out}")
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
        help="optional community contract list, for reward values the game does not contain",
    )
    p.add_argument("--out", type=Path, default=Path("cache.json"))
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("render", help="turn a cache into localization values")
    p.add_argument("--cache", type=Path, default=Path("cache.json"))
    p.add_argument("--profile", help="built-in profile name or path to a profile JSON")
    p.add_argument("--out", type=Path, default=Path("rendered.json"))
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("plan", help="preview changes without writing")
    p.add_argument("--rendered", type=Path, default=Path("rendered.json"))
    p.add_argument("--target", type=Path, required=True, help="global.ini to modify")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", help="write changes (requires --confirm)")
    p.add_argument("--rendered", type=Path, default=Path("rendered.json"))
    p.add_argument("--target", type=Path, required=True)
    p.add_argument("--profile", help="supplies the default merge mode")
    p.add_argument("--mode", choices=[m.value for m in MergeMode])
    p.add_argument("--stock", type=Path, help="pristine global.ini, for overwrite mode")
    p.add_argument("--backup-dir", type=Path)
    p.add_argument("--confirm", action="store_true", help="required to write anything")
    p.add_argument(
        "--allow-game-folder",
        action="store_true",
        help="permit writing inside a detected Star Citizen install",
    )
    p.set_defaults(func=cmd_apply)

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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
