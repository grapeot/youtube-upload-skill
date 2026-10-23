# youtube-upload-skill

An agent skill (`skills/youtube-upload/SKILL.md`) plus a Python command-line tool (`yt-upload`) that uploads a local video to your own YouTube channel through the official YouTube Data API v3. It uploads privately, sets the thumbnail, attaches captions, adds the video to one of your playlists, reads everything back, and stops. You watch the result in YouTube Studio and publish it yourself.

## Who it is for

Individual creators who use a coding agent (Claude Code, Codex, Cursor, OpenCode and similar) to help with publishing. The agent handles the repetitive parts: checking title and description limits, writing chapters in the format YouTube recognizes, uploading a large file without starting over after a network hiccup, uploading captions and the thumbnail, and confirming each field landed. You keep the decisions: approving each upload and making the video public.

It is not meant for bulk uploading, unattended publishing, or re-uploading content you do not own.

## What it will and will not do

- Uploads are **private** by default. `unlisted` is available when you ask for it on a specific run with `--visibility unlisted`. The tool cannot make a video public and cannot schedule one; you do that in YouTube Studio.
- `upload` prints a plan and a quota estimate and touches nothing until it is run with `--execute`.
- The same file is not uploaded twice by accident: the tool remembers each file's sha256 and refuses a second upload unless you pass `--new-upload`. An interrupted upload continues with `--resume`.
- It never deletes videos, never edits existing videos, never changes channel settings, and never creates playlists.
- "Made for kids" and the altered or synthetic content disclosure must be stated in every metadata file. There are no defaults.
- Your OAuth token is stored in `~/.youtube-upload/` with owner-only permissions and is never printed.

Full design and safety rules: `docs/design.md`.

## One-time setup

You need a YouTube channel and your own Google Cloud OAuth client. Roughly 20 minutes.

1. **Channel.** Sign in to YouTube with the Google account that owns the channel and create the channel if it does not exist yet. In YouTube Studio, open Settings > Channel > Feature eligibility and verify with a phone number. Without this, videos longer than 15 minutes and custom thumbnails are not allowed.
2. **Cloud project.** In the [Google Cloud console](https://console.cloud.google.com/), create a new project used only for this tool.
3. **Enable the API.** APIs & Services > Library > search "YouTube Data API v3" > Enable.
4. **Consent screen.** Open Google Auth Platform (APIs & Services > OAuth consent screen) and click Get started: any app name, your own support and contact email, audience **External**. Then go to Google Auth Platform > Audience and click **Publish app**, so the publishing status reads **In production**. You do not need to submit the app for verification. In Testing status, refresh tokens expire after 7 days and you would have to re-authorize every week.
5. **OAuth client.** Google Auth Platform > Clients > Create client > Application type **Desktop app**. Download the JSON and save it as `~/.youtube-upload/client_secret.json`:

   ```bash
   mkdir -p ~/.youtube-upload && chmod 700 ~/.youtube-upload
   mv /path/to/downloaded-client.json ~/.youtube-upload/client_secret.json
   chmod 600 ~/.youtube-upload/client_secret.json
   ```

6. **Authorize.** Run `yt-upload auth`. A browser window opens; pick the account that owns the channel. Google shows a "Google hasn't verified this app" page because the app is yours and unverified: choose Advanced > Go to (your app name). Grant access. `yt-upload status` should now show your channel.

The config directory can be changed with `--home` or `YT_UPLOAD_HOME`; it must be outside any git repository.

## Install

Requires Python 3.10 or newer and [uv](https://github.com/astral-sh/uv). `ffprobe` (from ffmpeg) is optional; when present, the tool checks chapter lengths and the 15-minute limit against the real duration.

```bash
git clone https://github.com/grapeot/youtube-upload-skill
cd youtube-upload-skill
uv venv
source .venv/bin/activate
uv pip install -e '.[dev]'
python -m pytest -q
```

### Installing the skill into your agent

Give your coding agent this repository's URL and ask it to install the skill. The agent should start from your workspace's `AGENTS.md` or `CLAUDE.md`, follow any routing file it points to, and add `skills/youtube-upload/SKILL.md` to the workspace's skill discovery: an entry in `rules/skills/INDEX.md` or `skills/INDEX.md` if one exists, otherwise a short pointer in `AGENTS.md` or `CLAUDE.md`.

## Quick start

```bash
yt-upload status                                        # token valid? which channel?
yt-upload upload --meta episode.json                    # dry-run: plan, quota, duplicate check
yt-upload upload --meta episode.json --execute          # private upload, thumbnail, captions, playlist, readback
yt-upload readback --video-id VIDEO_ID --meta episode.json
```

A metadata example is in `examples/episode.example.json`; the full format is in `skills/youtube-upload/SKILL.md`.

## License

MIT
