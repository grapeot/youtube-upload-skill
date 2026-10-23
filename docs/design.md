# Design

This document records the goals, boundaries, architecture and safety rules of youtube-upload-skill. Usage is in `README.md` and `skills/youtube-upload/SKILL.md`.

## Goal

Split "put a local video on my own YouTube channel" into two halves. The mechanical half goes to an agent: validating the metadata, rendering chapters into the description, uploading the file resumably, setting the thumbnail, attaching caption tracks, adding the video to an existing playlist, and reading everything back. The judgement half stays with the channel owner: watching the result in YouTube Studio and deciding when, and whether, the video goes public.

## Non-goals

- No publishing and no scheduling. The tool never makes a video public and never sets a publish time. The owner does both in YouTube Studio after reviewing the upload.
- No deleting videos, no editing existing videos, no channel settings, no creating or editing playlists (videos are added to an existing playlist by ID only).
- No bulk or unattended uploading, no re-uploading other people's content.
- No browser automation of YouTube Studio.

## Why the official API

The YouTube Data API v3 is the supported way to upload. Two facts make it the right route for a personal channel:

1. Projects that have not been through Google's API audit are no longer forced into private-only uploads. The `videos.insert` reference states that videos uploaded from unverified API projects are not restricted to private viewing mode.
2. Quota is ample for a personal cadence. `videos.insert` has its own bucket (100 calls per day, 1 unit each); everything else shares 10,000 units per day. A full upload with a thumbnail, one caption track, a playlist insert and a readback costs roughly 500 to 600 units from the shared pool.

Browser automation of Studio would depend on page structure that changes often and conflicts with YouTube's terms on automated access.

## Architecture

```text
owner                     agent / CLI                                   YouTube Data API v3
  │                         │                                               │
  │ one-time: Cloud project, OAuth Desktop client, consent screen in production
  │ yt-upload auth ─ browser consent (loopback redirect) ─────────────────▶│ refresh token -> ~/.youtube-upload/token.json (0600)
  │                         │ yt-upload status ── channels.list ───────────▶│ token valid? channel? long uploads?
  │                         │ yt-upload upload --meta M   (dry-run: plan, quota, duplicate check; no network)
  │ approves this upload    │
  │                         │ yt-upload upload --meta M --execute ────────▶│ videos.insert (resumable, private by default)
  │                         │                                               │ thumbnails.set, captions.insert, playlistItems.insert
  │                         │                                               │ videos.list / captions.list / playlistItems.list (readback)
  │ reviews in Studio, publishes or schedules there
```

| Module | Responsibility |
|---|---|
| `policy.py` | The only place that names visibility values. Allowed: private (default) and unlisted. Checks every status body before it is sent |
| `paths.py` | Config directory resolution (`--home`, `$YT_UPLOAD_HOME`, `~/.youtube-upload`), permission hardening, refusal to live inside a git repository |
| `auth.py` | OAuth installed-app flow with a loopback redirect, token storage (0600), refresh, credential status |
| `meta.py` | Read and validate the metadata JSON; every problem is reported at once |
| `describe.py` | Render chapters into the description |
| `probe.py` | Optional `ffprobe` duration probe |
| `plan.py` | Dry-run plan: request bodies, steps, quota estimate, warnings |
| `ledger.py` | sha256 duplicate guard and resumable-upload state, stored next to the token |
| `api.py` | Build the API client; turn `HttpError` into structured, untruncated error details |
| `upload.py` | Execute: resumable insert with retry, follow-up steps, ledger updates |
| `readback.py` | Read the video back and compare it with the metadata |
| `clientfile.py` | OAuth client JSON: detect Desktop vs web, find downloads, install with a backup of the replaced file |
| `browser.py` | Guided setup: visible Chrome with its own profile and a 127.0.0.1 debugging port, own-browser check, reopening a closed window, a `webbrowser` controller that opens the OAuth consent in that Chrome |
| `console.py` | Guided setup: Cloud console step URLs, read-only page snapshots, guarded click and fill |
| `setup.py` | `yt-upload setup ...` commands |
| `cli.py` | Command-line entry; every command prints exactly one JSON object |

## Safety rules

These are invariants. No change may break them.

1. **Never public, never scheduled.** Uploads are private unless the run explicitly asks for `--visibility unlisted`. The literal `public` and the `publishAt` field may appear only in `policy.py`; a source-scan test enforces this. The metadata file cannot carry a visibility or a schedule at all, so a reused metadata file can never widen visibility by itself.
2. **Dry-run by default.** `upload` without `--execute` makes no network call. The caller must have the owner's approval for this specific video before passing `--execute`.
3. **One upload per file.** The sha256 of the video file is recorded in the ledger. A second `--execute` on the same file is refused; `--resume` continues an interrupted upload or unfinished follow-up steps, and only `--new-upload` uploads the same file again.
4. **Credentials stay private.** The OAuth client secret, the refresh token and the ledger (which holds resumable session URLs) live in the config directory (mode 0700, files 0600), never inside a git repository. No command prints a token, a client secret or a session URL.
5. **Narrow write surface.** The tool calls exactly these write methods: `videos.insert`, `thumbnails.set`, `captions.insert`, `playlistItems.insert`. It never deletes, never updates existing videos, never touches channel settings and never creates playlists. Two layers enforce this: a source-scan test, and a transport wrapper (`api.GuardedHttp`) through which every request passes, which refuses any non-GET request outside those endpoints and any request body that names a disallowed visibility or a publish time.
6. **Disclosures are explicit.** `made_for_kids` and `contains_synthetic_media` have no defaults; metadata without them is rejected.
7. **Errors are passed through.** HTTP status, reason and message from Google are returned in the JSON, not replaced with a generic message.

## Guided setup

The one-time Cloud console configuration is the hardest part for most people, so `yt-upload setup` lets an agent do it in a browser the human can watch. The design follows the same rules as the rest of the tool:

- A dedicated Chrome profile (`<home>/browser-profile`, 0700) and a debugging port bound to 127.0.0.1. Before every action the tool checks that the Chrome on that port was started with that profile; it never attaches to the person's everyday browser.
- The human signs in. Google sign-in, account and consent pages are read-only for the tool: it neither types nor clicks there, and does not scrape them. Nothing reads, prints or exports cookies or storage.
- The agent works through small primitives instead of a hard-coded script: `goto STEP --project ID` opens a known console URL with the project pinned in the query string (so a stale project selector cannot redirect it), `snapshot` returns headings, buttons with their disabled state, fields and alerts, `click --text` and `fill --label` act on exactly one visible control. Every action is followed by a fresh snapshot.
- Guards: only `console.cloud.google.com` is driven; the label checked is the accessible name of the element that actually receives the click (its closest interactive ancestor, including `aria-labelledby`, `title` and `value`), matched against word stems (delet, remov, disabl, reset, revok, regenerat, billing, payment, upgrad, free trial, activat, back to testing, make internal and similar); nothing is clicked while a visible dialog mentions one of them; matching is exact, never by substring; password fields are refused. Snapshots are scrubbed of OAuth client secrets and tokens before they are printed, because the console shows a new client's secret in plain text.
- The browser profile holds a signed-in Google session: it must be outside any git repository, and the tool only tightens permissions on the profile directory itself. When setup is done, quit the setup Chrome; while it runs, any local process can reach its debugging port.
- The client JSON is installed by copying (the download stays where it is); a replaced `client_secret.json` is renamed to a timestamped backup, never deleted. Only a Desktop (`installed`) client is accepted.
- If the human closes the window, Chrome on macOS keeps running without windows and CDP attach fails; the tool detects that there are no tabs and opens a new window. If Chrome has quit, `setup browser` relaunches it with the same profile, so the sign-in survives.

## OAuth setup decisions

- Client type: Desktop app, loopback redirect on an ephemeral port.
- Scope: `https://www.googleapis.com/auth/youtube.force-ssl` only. Captions need it; it also grants broad channel management, which is why the token is treated like a password.
- A dedicated Google Cloud project for this tool, not shared with other integrations.
- Consent screen: External, published to "In production" without submitting for verification. In "Testing" status, refresh tokens expire after 7 days. The owner will see Google's "unverified app" warning once during `auth`.

## Resumable uploads

`videos.insert` uses the resumable protocol in chunks. Transient failures (HTTP 500, 502, 503, 504, connection errors) are retried with exponential backoff; after an error the client asks the server how many bytes it holds and continues from there. The session URL is written to the ledger after the first chunk, so if the process dies, `upload --execute --resume` re-attaches to the same session instead of starting over. Google keeps an upload session for about a week; an expired session is reported, and `--new-upload` starts a fresh one.

## Things YouTube decides, not the tool

- Videos longer than 15 minutes need a phone-verified channel. `status` reports `long_uploads_status`; `upload --execute` refuses a long video when it is not `allowed`. The duration comes from `ffprobe`; without it the check cannot run and the dry-run says so.
- Custom thumbnails need a verified channel. A failed `thumbnails.set` is reported as a failed step, the rest of the run continues.
- `defaultAudioLanguage` is not listed among the writable fields of `videos.insert`; the tool sends it and the readback reports whether it stuck.
- Chapters are parsed by YouTube from the description: the first timestamp must be 00:00, there must be at least three, ascending, each at least 10 seconds long.
