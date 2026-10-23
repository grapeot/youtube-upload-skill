# AGENTS.md

Notes and invariants for coding agents working in `https://github.com/grapeot/youtube-upload-skill`.

## Commands

Check for a `.venv` at the repository root first. Activate it if present; otherwise create it with uv. Install dependencies with `uv pip install`.

```bash
uv venv
source .venv/bin/activate
uv pip install -e '.[dev]'
python -m pytest -q
```

Tests are fully offline. They never contact Google and need no credentials: API traffic is replayed with `googleapiclient.http.HttpMockSequence`.

## Repository invariants

1. **Default branch is `master`.** Every change goes through a feature branch and a PR into `master`.
2. **Never public, never scheduled.** Visibility values are named only in `src/youtube_upload/policy.py`. Do not write the literal `public` or the field `publishAt` anywhere else under `src/`; `tests/test_source_scan.py` fails if you do. Do not add a metadata key for visibility or scheduling. `unlisted` is reachable only through the per-run `--visibility unlisted` flag.
3. **Narrow write surface.** The only write calls are `videos.insert`, `thumbnails.set`, `captions.insert` and `playlistItems.insert`. No delete, no `videos.update`, no channel, playlist, watermark or banner writes. The source scan enforces this, and so does `api.GuardedHttp` at the transport: keep every client built through `api.build_service`.
4. **`upload` is dry-run by default.** Only `--execute` touches the network. The duplicate guard (sha256 ledger) must stay in front of every new `videos.insert`.
5. **Credentials never leave the config directory.** Tokens, client secrets and resumable session URLs are never printed, logged, put in JSON output or committed. The config directory must not be inside a git repository.
6. **One JSON object per command.** Every `yt-upload` subcommand, including on error, prints exactly one JSON object to stdout (`--help` excepted). Progress and OAuth prompts go to stderr.
7. **Pass errors through.** Keep Google's HTTP status, reason and message in error output.
8. **No personal data in the repository.** No real channel IDs, video IDs, playlist IDs, emails, names, client secrets, tokens, videos, thumbnails, captions or absolute machine paths. Docs and tests use placeholders such as `/path/to/video.mp4`, `UC_TEST_CHANNEL`, `PLxxxxxxxxxxxxxxxxxxxx`.
9. **Update `docs/working.md`** with a dated changelog entry when you change behavior.

## Layout

```text
youtube-upload-skill/
├── README.md
├── AGENTS.md
├── LICENSE
├── pyproject.toml
├── docs/design.md                 # goals, boundaries, architecture, safety rules
├── docs/test.md                   # what the offline suite covers and what counts as verified
├── docs/working.md                # changelog and lessons learned
├── skills/youtube-upload/SKILL.md # agent instructions and metadata format
├── examples/                      # *.example.json metadata
├── src/youtube_upload/            # implementation (module map in docs/design.md)
└── tests/                         # offline tests
```
