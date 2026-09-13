"""Exact-build, opt-in legacy StarStrings mining signatures."""

from __future__ import annotations

from .ini import LocalizationFile
from .model import (
    ContractSet,
    Evidence,
    LegacyMiningSignature,
    ProviderCapability,
    ProviderStatus,
)

PROVIDER = "legacy-starstrings-mining-signatures"
VERSION = "1"
SUPPORTED_BUILD = "1.0.191.55227"
SOURCE_COMMIT = "b83d58baedf71b090903b9ec34186520907271f9"
SOURCE_PATH = (
    "https://github.com/MrKraken/StarStrings/blob/"
    f"{SOURCE_COMMIT}/src/For_Tool_Creators/mining.ini"
)

SIGNATURES = (
    ("agricium", "Agricium", 3885),
    ("aluminium", "Aluminium", 4285),
    ("aslarite", "Aslarite", 3840),
    ("beryl", "Beryl", 3540),
    ("bexalite", "Bexalite", 3600),
    ("borase", "Borase", 3570),
    ("copper", "Copper", 4240),
    ("corundum", "Corundum", 4225),
    ("gold", "Gold", 3585),
    ("hephaestanite", "Hephaestanite", 4180),
    ("ice", "Ice", 4300),
    ("iron", "Iron", 4270),
    ("laranite", "Laranite", 3825),
    ("lindinium", "Lindinium", 3400),
    ("ouratite", "Ouratite", 3370),
    ("quantainium", "Quantainium", 3170),
    ("quartz", "Quartz", 4210),
    ("riccite", "Riccite", 3385),
    ("savrillium", "Savrillium", 3200),
    ("silicon", "Silicon", 4255),
    ("stileron", "Stileron", 3185),
    ("taranite", "Taranite", 3555),
    ("tin", "Tin", 4195),
    ("titanium", "Titanium", 3855),
    ("torite", "Torite", 3900),
    ("tungsten", "Tungsten", 3870),
)


def attach_legacy_mining_pack(
    contracts: ContractSet,
    strings: LocalizationFile,
    build_version: str,
) -> ContractSet:
    """Attach rules only when both the build and stock value match exactly."""

    if build_version != SUPPORTED_BUILD:
        contracts.capabilities.append(
            ProviderCapability(
                PROVIDER,
                VERSION,
                ProviderStatus.UNAVAILABLE,
                build_version,
                facts_seen=len(SIGNATURES),
                diagnostics=(
                    "info:legacy-pack-build-unreviewed: numeric signatures remain disabled",
                ),
                unmatched_facts=len(SIGNATURES),
                unmatched_reason_counts=(("unsupported-build", len(SIGNATURES)),),
            )
        )
        return contracts

    rules = []
    missing = 0
    mismatch = 0
    for name, expected, signature in SIGNATURES:
        key = f"mineabletype_primary_{name}"
        stock = strings.get(key)
        if stock is None:
            missing += 1
            continue
        if stock != expected:
            mismatch += 1
            continue
        evidence = Evidence(
            PROVIDER,
            f"mining-signature-{name}",
            SOURCE_PATH,
            key,
            signature,
        )
        rules.append(
            LegacyMiningSignature(
                f"mining-signature-{name}",
                SUPPORTED_BUILD,
                key,
                stock,
                signature,
                (evidence,),
            )
        )

    unmatched = missing + mismatch
    diagnostics = []
    if missing:
        diagnostics.append(
            f"warning:legacy-pack-key-missing: {missing} exact keys were suppressed"
        )
    if mismatch:
        diagnostics.append(
            f"warning:legacy-pack-stock-drift: {mismatch} changed values were suppressed"
        )
    status = ProviderStatus.AVAILABLE
    if unmatched:
        status = ProviderStatus.DEGRADED if rules else ProviderStatus.UNAVAILABLE
    contracts.legacy_signatures = rules
    contracts.capabilities.append(
        ProviderCapability(
            PROVIDER,
            VERSION,
            status,
            build_version,
            facts_seen=len(SIGNATURES),
            contracts_enhanced=len(rules),
            evidence_links=len(rules),
            diagnostics=tuple(diagnostics),
            matched_facts=len(rules),
            unmatched_facts=unmatched,
            unmatched_reason_counts=tuple(
                (reason, count)
                for reason, count in (
                    ("localization-missing", missing),
                    ("stock-value-drift", mismatch),
                )
                if count
            ),
        )
    )
    return contracts


__all__ = [
    "PROVIDER",
    "SIGNATURES",
    "SOURCE_COMMIT",
    "SOURCE_PATH",
    "SUPPORTED_BUILD",
    "VERSION",
    "attach_legacy_mining_pack",
]
