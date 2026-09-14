"""Exact-build, opt-in legacy StarStrings mining signatures."""

from __future__ import annotations

import hashlib

from .ini import LocalizationFile
from .model import (
    ContractSet,
    Evidence,
    LegacyMiningSignature,
    LegacyPresentationRule,
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
PRESENTATION_PROVIDER = "legacy-starstrings-presentation"
PRESENTATION_VERSION = "1"
STOCK_SOURCE_COMMIT = "38dd1cdffb8849e5925c380b684f0ed7d4cb0243"
STOCK_SOURCE_PATH = (
    "https://github.com/iGhost/global-ini/blob/"
    f"{STOCK_SOURCE_COMMIT}/Data/Localization/english/global.ini"
)
COMPONENT_SOURCE_PATH = (
    "https://github.com/MrKraken/StarStrings/blob/"
    f"{SOURCE_COMMIT}/src/For_Tool_Creators/components.ini"
)
PLAYER_SOURCE_PATH = (
    "https://github.com/MrKraken/StarStrings/blob/"
    f"{SOURCE_COMMIT}/src/For_Players/Data/Localization/english/global.ini"
)
MINING_SOURCE_PATH = (
    "https://github.com/MrKraken/StarStrings/blob/"
    f"{SOURCE_COMMIT}/src/For_Tool_Creators/mining.ini"
)

DIRECT_PRESENTATION_RULES = (
    (
        "raw-hephaestanite",
        "items_commodities_hephaestanite_raw",
        "Hephaestanite (R)",
        "Heph (Raw)",
        MINING_SOURCE_PATH,
    ),
    ("raw-ice", "items_commodities_raw_ice", "Raw Ice", "Ice (Raw)", MINING_SOURCE_PATH),
    (
        "raw-ouratite",
        "items_commodities_raw_ouratite",
        "Raw Ouratite",
        "Ouratite (Raw)",
        MINING_SOURCE_PATH,
    ),
    (
        "raw-silicon",
        "items_commodities_raw_silicon",
        "Raw Silicon",
        "Silicon (Raw)",
        MINING_SOURCE_PATH,
    ),
    (
        "illegal-weevil-eggs",
        "items_commodities_GaspingWeevilEggs",
        "Gasping Weevil Eggs",
        "<EM3>[!]</EM3> Gasping Weevil Eggs",
        PLAYER_SOURCE_PATH,
    ),
    (
        "illegal-altruciatoxin",
        "items_commodities_altruciatoxin",
        "Altruciatoxin",
        "<EM3>[!]</EM3> Altruciatoxin",
        PLAYER_SOURCE_PATH,
    ),
    (
        "illegal-tree-pollen",
        "items_commodities_altruciatoxin_unprocessed",
        "Revenant Tree Pollen",
        "<EM3>[!]</EM3> Revenant Tree Pollen",
        PLAYER_SOURCE_PATH,
    ),
    (
        "illegal-etam",
        "items_commodities_etam",
        "E'tam",
        "<EM3>[!]</EM3> E'tam",
        PLAYER_SOURCE_PATH,
    ),
    ("illegal-maze", "items_commodities_maze", "Maze", "<EM3>[!]</EM3> Maze", PLAYER_SOURCE_PATH),
    ("illegal-neon", "items_commodities_neon", "Neon", "<EM3>[!]</EM3> Neon", PLAYER_SOURCE_PATH),
    ("illegal-slam", "items_commodities_slam", "SLAM", "<EM3>[!]</EM3> SLAM", PLAYER_SOURCE_PATH),
    ("illegal-widow", "items_commodities_widow", "WiDoW", "<EM3>[!]</EM3> WiDoW", PLAYER_SOURCE_PATH),
    (
        "multitool-cutter",
        "item_Namegrin_multitool_01_cutter",
        "OxyTorch Cutter Attachment",
        "Cutter",
        COMPONENT_SOURCE_PATH,
    ),
    (
        "multitool-healing",
        "item_Namegrin_multitool_01_healing",
        "LifeGuard Medical Attachment",
        "Healing",
        COMPONENT_SOURCE_PATH,
    ),
    (
        "multitool-mining",
        "item_Namegrin_multitool_01_mining",
        "OreBit Mining Attachment",
        "Mining",
        COMPONENT_SOURCE_PATH,
    ),
    (
        "multitool-salvage",
        "item_Namegrin_multitool_01_salvage",
        "Cambio-Lite SRT Attachment",
        "Salvage",
        COMPONENT_SOURCE_PATH,
    ),
    (
        "multitool-tractorbeam",
        "item_Namegrin_multitool_01_tractorbeam",
        "TruHold Tractor Beam Attachment",
        "Tractorbeam",
        COMPONENT_SOURCE_PATH,
    ),
)

_JOURNAL_HASHES = {
    "Journal_General_Mining_Compendium_Content": (
        "011d711156cf12e02110dd4d1e4403b819143547d063025edf4a7a02409b4858",
        "journal-mining-rarity",
    ),
    "Journal_General_Refueling_Content": (
        "1aa2bb2b90bd053af69e882c076505e5edcf465025e6298cd335656e1bf4a2b0",
        "journal-refueling-tips",
    ),
}

_MINING_GROUPS = (
    ("Legendary", ("Quantainium", "Savrilium", "Stileron")),
    ("Epic", ("Lindinium", "Ouratite", "Riccite")),
    ("Rare", ("Beryl", "Bexalite", "Borase", "Gold", "Taranite")),
    (
        "Uncommon",
        ("Agricium", "Aslarite", "Laranite", "Titanium", "Torite", "Tungsten"),
    ),
    (
        "Common",
        ("Aluminium", "Copper", "Corundum", "Hephaestanite", "Ice", "Iron", "Quartz", "Silicon", "Tin"),
    ),
    (
        "Hand Mineables",
        ("Aphorite", "Beradon", "Caranite", "Dolivine", "Feynmaline", "Glacosite", "Hadanite", "Janalite", "Sadaryx"),
    ),
)

_REFUELING_TIPS = (
    "Quick tips\\n"
    "- Refill pods with the Manual controls in landing services.\\n"
    "- Refueling mode default key: M.\\n"
    "- Target the other ship, request docking with Right Alt + N, then allow auto-dock.\\n"
    "- Open pod flow at the refueling terminal after docking.\\n"
    "- Leave refueling mode to undock when transfer is complete."
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


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _group_mining_compendium(value: str) -> str | None:
    sections = value.split("\\n\\n")
    if len(sections) < 2:
        return None
    entries = {}
    for section in sections[1:]:
        name, separator, _detail = section.partition(" - ")
        if not separator or name in entries:
            return None
        entries[name] = section
    expected = {name for _label, names in _MINING_GROUPS for name in names}
    if set(entries) != expected:
        return None
    grouped = [sections[0]]
    for label, names in _MINING_GROUPS:
        grouped.extend((f"** {label} **", *(entries[name] for name in names)))
    return "\\n\\n".join(grouped)


def attach_legacy_presentation_pack(
    contracts: ContractSet,
    strings: LocalizationFile,
    build_version: str,
) -> ContractSet:
    """Attach small exact rules and hash-bound local journal transformations."""

    total = len(DIRECT_PRESENTATION_RULES) + len(_JOURNAL_HASHES)
    if build_version != SUPPORTED_BUILD:
        contracts.capabilities.append(
            ProviderCapability(
                PRESENTATION_PROVIDER,
                PRESENTATION_VERSION,
                ProviderStatus.UNAVAILABLE,
                build_version,
                facts_seen=total,
                diagnostics=(
                    "info:legacy-presentation-build-unreviewed: wording remains disabled",
                ),
                unmatched_facts=total,
                unmatched_reason_counts=(("unsupported-build", total),),
            )
        )
        return contracts

    rules: list[LegacyPresentationRule] = []
    reasons: dict[str, int] = {}

    def reject(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for rule_id, key, expected, replacement, source in DIRECT_PRESENTATION_RULES:
        stock = strings.get(key)
        if stock is None:
            reject("localization-missing")
            continue
        if stock != expected:
            reject("stock-value-drift")
            continue
        digest = _sha256(stock)
        rules.append(
            LegacyPresentationRule(
                rule_id,
                SUPPORTED_BUILD,
                key,
                stock,
                digest,
                replacement,
                (
                    Evidence(PRESENTATION_PROVIDER, rule_id, source, key, _sha256(replacement)),
                    Evidence(PRESENTATION_PROVIDER, rule_id, STOCK_SOURCE_PATH, key, digest),
                ),
            )
        )

    for key, (expected_hash, rule_id) in _JOURNAL_HASHES.items():
        stock = strings.get(key)
        if stock is None:
            reject("localization-missing")
            continue
        if _sha256(stock) != expected_hash:
            reject("stock-value-drift")
            continue
        replacement = (
            _group_mining_compendium(stock)
            if rule_id == "journal-mining-rarity"
            else f"{_REFUELING_TIPS}\\n\\n{stock}"
        )
        if replacement is None:
            reject("transform-schema-drift")
            continue
        rules.append(
            LegacyPresentationRule(
                rule_id,
                SUPPORTED_BUILD,
                key,
                stock,
                expected_hash,
                replacement,
                (
                    Evidence(PRESENTATION_PROVIDER, rule_id, MINING_SOURCE_PATH, key, _sha256(replacement)),
                    Evidence(PRESENTATION_PROVIDER, rule_id, STOCK_SOURCE_PATH, key, expected_hash),
                ),
            )
        )

    unmatched = sum(reasons.values())
    status = ProviderStatus.AVAILABLE
    if unmatched:
        status = ProviderStatus.DEGRADED if rules else ProviderStatus.UNAVAILABLE
    contracts.legacy_presentations = rules
    contracts.capabilities.append(
        ProviderCapability(
            PRESENTATION_PROVIDER,
            PRESENTATION_VERSION,
            status,
            build_version,
            facts_seen=total,
            contracts_enhanced=len(rules),
            evidence_links=sum(len(item.evidence) for item in rules),
            diagnostics=tuple(
                f"warning:legacy-presentation-{reason}: {count} rules were suppressed"
                for reason, count in sorted(reasons.items())
            ),
            matched_facts=len(rules),
            unmatched_facts=unmatched,
            unmatched_reason_counts=tuple(sorted(reasons.items())),
        )
    )
    return contracts


__all__ = [
    "PROVIDER",
    "PRESENTATION_PROVIDER",
    "PRESENTATION_VERSION",
    "DIRECT_PRESENTATION_RULES",
    "SIGNATURES",
    "SOURCE_COMMIT",
    "SOURCE_PATH",
    "SUPPORTED_BUILD",
    "VERSION",
    "attach_legacy_mining_pack",
    "attach_legacy_presentation_pack",
]
