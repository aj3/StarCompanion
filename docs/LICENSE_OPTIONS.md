# Project license decision record

StarCompanion selected the **Apache License 2.0** (`Apache-2.0`) on 2026-08-03.
The repository now includes `LICENSE` and `NOTICE`, the package metadata declares
the SPDX identifier, and release SBOMs record the same license.

This is an engineering comparison, not legal advice. The copyright holder
should confirm the final choice if licensing consequences are material.

## Selected: Apache License 2.0 (`Apache-2.0`)

Apache-2.0 is permissive, includes an explicit contributor patent grant and
termination terms, and has a structured `NOTICE` mechanism. It also aligns with
Smart Citizen's existing Apache-2.0 licensing if behavior-level research later
becomes attributed code reuse. Required notices and upstream license text would
still need to be preserved for copied code.

It was selected to permit broad reuse while retaining the explicit patent and
NOTICE terms.

## Simpler permissive option: MIT (`MIT`)

MIT is short and permissive: redistribution and proprietary derivatives are
allowed when the copyright and permission notice are retained. It does not
contain Apache-2.0's detailed express patent grant or NOTICE framework.

Choose this when minimal license text and administration matter more than an
explicit patent clause. Any Apache-2.0 code reused from Smart Citizen would
remain under its own Apache-2.0 terms and attribution requirements.

## File-level copyleft option: Mozilla Public License 2.0 (`MPL-2.0`)

MPL-2.0 requires modifications to covered source files to remain available
under MPL while allowing those files to be combined into a larger proprietary
work. It is a middle ground between permissive licensing and project-wide
copyleft.

Choose this when improvements to StarCompanion source files should remain open
without requiring an entire combined application to use the same license.

## Strong copyleft option: GNU GPL v3 (`GPL-3.0-only` or `GPL-3.0-or-later`)

GPLv3 requires distributed derivative works to provide corresponding source
under GPL-compatible terms. It protects downstream software freedom but limits
proprietary reuse and requires more deliberate distribution compliance.

Choose this only if reciprocal open-source distribution is a core product goal.
The `only` versus `or-later` choice must be explicit.

## Recorded decision details

- Copyright notice: `Copyright 2026 StarCompanion contributors`.
- Proprietary forks are permitted under Apache-2.0's conditions.
- The express contributor patent grant is desired.
- Contributions intentionally submitted for inclusion use Apache-2.0's default
  Section 5 terms; no separate CLA or DCO policy is established yet.
- Smart Citizen informed behavior and feature planning, but no Smart Citizen
  implementation code is included. Its role is recorded in NOTICE without
  claiming its code or copyright.

The selected license has now been applied. Third-party code and content must
retain their own notices and must never be relabeled as StarCompanion-owned.
