# Working notes

## Changelog

### 2026-10-23

- Scaffold: README, AGENTS.md, design notes, test plan, skill stub, CI, MIT license.
- v0.1 CLI: `auth`, `status`, `upload` (dry-run default, `--execute`, `--resume`, `--new-upload`, `--visibility unlisted`), `readback`, `categories`.
- Metadata validation with every problem reported at once; chapters rendered into the description; `made_for_kids` and `contains_synthetic_media` required with no defaults.
- Resumable `videos.insert` with retry and cross-process resume through the ledger; sha256 duplicate guard; follow-up steps (thumbnail, captions, playlist by ID) recorded per step.
- Offline suite: 147 passed (HttpMockSequence for the resumable protocol, 5xx with status query, 401 refresh, follow-ups, readback; source scan for visibility literals and forbidden write calls; CLI one-JSON contract).

## Lessons Learned

(none yet)
