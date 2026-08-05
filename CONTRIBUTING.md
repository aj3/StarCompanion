# Contributing

StarCompanion accepts focused issues and pull requests. Participation follows
the [code of conduct](CODE_OF_CONDUCT.md).

1. Describe the behavior or security property being changed.
2. Use synthetic fixtures; never commit game archives, player logs, local
   settings, credentials, or proprietary game data.
3. Keep file and section comments short and explain intent, not syntax.
4. Preserve channel/language isolation, preview, confirmation, fingerprint,
   backup, rollback, cancellation, and offline safety gates.
5. Run `pytest` and the relevant benchmarks before opening a pull request.
6. Update public documentation, tests, the SBOM, and dependency locks when the
   affected surface changes.

Contributions from people without commit access require maintainer review.
Security-sensitive reports follow [SECURITY.md](SECURITY.md), not public issues.

By contributing, you agree that your contribution is licensed under the
project's [Apache License 2.0](LICENSE).
