# Working notes

## Changelog

### 2026-10-23

- Scaffold: README, AGENTS.md, design notes, test plan, skill stub, CI, MIT license.
- v0.1 CLI: `auth`, `status`, `upload` (dry-run default, `--execute`, `--resume`, `--new-upload`, `--visibility unlisted`), `readback`, `categories`.
- Metadata validation with every problem reported at once; chapters rendered into the description; `made_for_kids` and `contains_synthetic_media` required with no defaults.
- Resumable `videos.insert` with retry and cross-process resume through the ledger; sha256 duplicate guard; follow-up steps (thumbnail, captions, playlist by ID) recorded per step.
- Offline suite: 147 passed (HttpMockSequence for the resumable protocol, 5xx with status query, 401 refresh, follow-ups, readback; source scan for visibility literals and forbidden write calls; CLI one-JSON contract).

- Guided setup: `yt-upload setup` (browser, status, plan, goto, snapshot, click, fill, install-client, auth) in a visible Chrome with its own profile; guarded against sign-in pages, destructive and billing controls; client JSON installed with a backup; closed window detected and reopened. Offline suite: 187 passed.

- End-to-end run on a real channel: guided setup (sign-in and consent by the human), then one 60-second 1080p clip uploaded unlisted with chapters, a thumbnail and a caption track. Thumbnail and caption steps succeeded; readback clean once processing finished.
- Readback: empty tags while processing are reported as unverified instead of a mismatch. Offline suite: 188 passed.

## Lessons Learned

- On macOS, closing the last Chrome window leaves the process running with zero tabs; Playwright's connect_over_cdp then fails with "Browser context management is not supported". Open a tab through the DevTools HTTP endpoint (PUT /json/new) first.
- The console's project selector and in-page links can point at a different project than the one you meant. Pin the project with `?project=ID` on every URL and check it in each snapshot.
- "Publish app" disabled with "complete your configuration on the Branding page" can be caused by a project that already carries another app's configuration (for example restricted scopes declared under Data Access). Prefer a fresh project.
- Immediately after `videos.insert`, `videos.list` returned no tags while `processingStatus` was `processing`; a minute later they were there. `containsSyntheticMedia` was not returned at all, even for the owner.
