---
name: youtube-upload
description: Upload a local video to the owner's own YouTube channel through the official YouTube Data API v3 with the yt-upload CLI. Private by default (unlisted only when requested for that run), dry-run plan first, then a resumable upload, thumbnail, captions, playlist insert and a readback. Never publishes, schedules, deletes or edits existing videos. Use for personal, low-volume uploads that the owner reviews in YouTube Studio before publishing.
---

# youtube-upload

## When to use

- The channel owner wants a local video uploaded to their own YouTube channel, with title, description, chapters, tags, thumbnail, captions and playlist handled for them, and will publish it themselves in YouTube Studio.
- Checking whether a previous upload landed as intended (`readback`).

Do not use it for bulk or unattended uploads, for content the owner does not have the rights to, or to publish or schedule a video.

## Hard rules

1. **Never public, never scheduled.** The tool cannot do either, and you must not work around it (no direct API calls, no `videos.update`). Publishing and scheduling are the owner's actions in YouTube Studio.
2. **Private unless the owner asks otherwise for this run.** Pass `--visibility unlisted` only when the owner explicitly asked for an unlisted upload of this specific video. Do not carry that choice over to later uploads.
3. **`--execute` needs approval for this specific video.** Show the dry-run plan first. Get the owner's go-ahead for this file, this title and these disclosures before executing.
4. **Disclosures are the owner's statements.** `made_for_kids` and `contains_synthetic_media` have no defaults. If you are not sure, ask. `contains_synthetic_media` is true when the video contains realistic altered or synthetic content a viewer could mistake for real; the API's examples are making a real person appear to say or do something they did not, altering footage of a real event or place, and generating a realistic scene that did not occur. Using AI for scripting or captions, or clearly unrealistic or animated content, does not by itself require it. Borderline cases (for example an AI avatar or a cloned voice of the owner) are the owner's call; YouTube's Help Center article on disclosing altered or synthetic content is the authority.
5. **Never print or copy credentials.** Do not `cat` files in the config directory (`~/.youtube-upload/` by default), do not paste tokens anywhere, do not put that directory inside a repository.
6. **No retries by re-uploading.** If an upload is interrupted, use `--resume`. Use `--new-upload` only when the owner wants a second copy.

## Workflow

```bash
yt-upload status                                   # token valid? which channel? long uploads allowed?
yt-upload upload --meta episode.json               # dry-run: plan, quota, duplicate check (no network)
yt-upload upload --meta episode.json --execute     # upload private, thumbnail, captions, playlist, readback
```

1. Run `status`. If `needs_auth` is true, ask the owner to run `yt-upload auth` (it opens a browser; only the owner can complete it). If `client_secret_present` is false, the one-time setup in the README has not been done.
2. Write the metadata JSON (format below) and run the dry-run. Show the owner: the title, the rendered description with chapters, visibility, the two disclosures, the step list, the quota estimate, every warning, and `previous_upload` if it is not null.
3. After the owner approves, run with `--execute` (plus `--visibility unlisted` only if asked).
4. Read the result and report it faithfully: `video_id`, `url`, `studio_url`, each entry in `steps` (a failed thumbnail usually means the channel is not verified yet), `readback.mismatches`, `readback.unverified`, `readback.not_applied`, and `human_steps`.
5. Hand over: the owner reviews the video in Studio and publishes or schedules it there.

## Metadata format

One UTF-8 JSON object. Relative paths resolve against the metadata file's directory.

```json
{
  "video": "/path/to/video.mp4",
  "title": "Episode title",
  "description": "First paragraph.\n\nSecond paragraph.",
  "chapters": [
    {"start": 0, "title": "Intro"},
    {"start": "01:30", "title": "Main part"},
    {"start": "05:00", "title": "Wrap-up"}
  ],
  "tags": ["tag one", "tag two"],
  "category": "28",
  "language": "en",
  "audio_language": "en",
  "cover": "thumbnail.jpg",
  "subtitles": [{"path": "subtitles.en.srt", "language": "en", "name": "English"}],
  "playlist_id": "PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "made_for_kids": false,
  "contains_synthetic_media": false,
  "license": "youtube",
  "embeddable": true,
  "notify_subscribers": false
}
```

| Field | Required | Rules | Notes |
|---|---|---|---|
| `video` | yes | file exists, common video extension, at most 256 GB | |
| `title` | yes | 1 to 100 characters, single line, no `<` or `>` | |
| `description` | no | no `<` or `>`; at most 5000 UTF-8 bytes after the chapter block is appended; must not itself contain lines starting with a timestamp when `chapters` is set | |
| `chapters` | no | at least 3; first at 0; strictly increasing; each at least 10 s long, the last one measured to the end of the video when `ffprobe` is available | `start` is whole seconds or `MM:SS` / `H:MM:SS`. Rendered as `MM:SS Title` lines after a blank line at the end of the description, which is how YouTube detects chapters |
| `tags` | no | no commas, no `<` `>`, no duplicates; total at most 500 counted YouTube's way (commas count, a tag containing a space counts 2 extra) | |
| `category` | no | numeric category ID | `yt-upload categories --region US` lists assignable IDs. Omitted: YouTube's default |
| `language` | no | BCP-47 code | language of title and description (`defaultLanguage`) |
| `audio_language` | no | BCP-47 code | `defaultAudioLanguage`; not a documented writable field, so the readback lists it under `not_applied` if it did not stick |
| `cover` | no | `.jpg`, `.jpeg` or `.png`, at most 50 MB | custom thumbnails need a verified channel |
| `subtitles` | no | list of `{path, language, name}`; `.srt` or `.vtt`, at most 100 MB; `language` required; `(language, name)` unique | each track costs 400 quota units |
| `playlist_id` | no | an existing playlist's ID (the part after `list=`) | the tool never creates playlists |
| `made_for_kids` | **yes** | `true` or `false` | no default |
| `contains_synthetic_media` | **yes** | `true` or `false` | no default; see hard rule 4 |
| `license` | no | `youtube` (default) or `creativeCommon` | |
| `embeddable` | no | boolean, default `true` | |
| `notify_subscribers` | no | boolean, default `true` | passed through as the `notifySubscribers` parameter of `videos.insert` |

Visibility and scheduling keys (`visibility`, `privacy_status`, `schedule`, `publish_at` and similar) are rejected on purpose: visibility is a per-run flag and publishing happens in Studio.

## Command reference

Every command prints exactly one JSON object to stdout, including on errors; progress and the OAuth prompt go to stderr. Exit code 0 when `ok` is true, 1 otherwise, 2 for usage errors. Global option `--home DIR` (default `$YT_UPLOAD_HOME` or `~/.youtube-upload`); the directory must not be inside a git repository.

| Command | What it does | Network |
|---|---|---|
| `auth [--timeout S] [--no-browser]` | OAuth consent in the browser (loopback redirect), stores the token, reports the channel | yes, interactive |
| `status` | config files present, token valid (refreshes it if needed), channel ID and title, `long_uploads_status`, interrupted uploads in the ledger | `channels.list` (1 unit) |
| `upload --meta M` | validate, render, plan, quota estimate, duplicate check | none |
| `upload --meta M --execute [--visibility unlisted]` | channel check, resumable `videos.insert`, `thumbnails.set`, `captions.insert`, `playlistItems.insert`, readback | yes |
| `upload --meta M --execute --resume` | continue an interrupted upload in the same session, or finish follow-up steps that failed; steps already done are skipped | yes |
| `upload --meta M --execute --new-upload` | upload the same file again as a new video | yes |
| `readback --video-id ID [--meta M] [--visibility V]` | read the video (and its captions and playlist membership when `--meta` lists them) and compare | read-only |
| `categories [--region CC]` | assignable category IDs | read-only, 1 unit |

### Reading `upload --execute` output

- `ok`: true only when the upload, every follow-up step and the readback all succeeded.
- `mode`: `new`, `resume_session` or `follow_ups`.
- `steps[]`: `{step, ok, error?}`; a failed step does not stop the others, and `--resume` retries only failed or missing steps (a caption or playlist step that may have landed is checked before it is retried, so no duplicates).
- `readback`: `mismatches` (present and different), `unverified` (the API did not return the field), `not_applied` (best-effort fields such as `audio_language`, with how to fix them in Studio).
- On an insert failure: `error_detail` carries Google's status, reason and message; `next` says whether to `--resume` or `--new-upload`.
- Refusals (duplicate, interrupted upload, long video on an unverified channel, no channel) come back as `ok: false` with an `error` explaining the flag to use.

## Quota

Default project quota: `videos.insert` has its own bucket of 100 calls per day; everything else shares 10,000 units per day, reset at midnight Pacific time. A typical upload with a thumbnail, one caption track and a playlist costs about 550 shared units, most of it the 400-unit caption insert. The dry-run prints the estimate.

## Troubleshooting

- `needs_auth` after it worked before: the refresh token was revoked, the account password changed, it went unused for six months, or the consent screen is still in Testing status (7-day expiry). Publish the consent screen to In production and run `yt-upload auth` again.
- `quotaExceeded`: wait for the daily reset; nothing is lost, rerun with `--resume` if an upload was interrupted.
- `thumbnail` step failed with `forbidden`: the channel is not verified for custom thumbnails. Verify it in YouTube Studio > Settings > Channel > Feature eligibility, then `--resume`.
- Long video refused: same verification, it unlocks uploads over 15 minutes.
- Session expired on `--resume`: Google keeps an upload session for about a week. Use `--new-upload`.
