# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Project foundation (Milestone 1): packaging, CLI, configuration with `${VAR}` interpolation
  and environment overlays, credential redaction, MongoDB connection handling.
- Revision files with random 12-hex ids, AST-based loading (no code execution for offline
  commands), checksums, and a revision graph with heads, branches, merges, prefix resolution.
- Migration tracking collection (`__mongomig_migrations`).
- Commands: `init`, `revision`, `heads`, `history`, `current`; `--json` output everywhere.
- Docker Compose MongoDB (single-node replica set) and CI for Python 3.11–3.13 × MongoDB 6/7/8.
