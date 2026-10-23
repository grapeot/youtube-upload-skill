# Test strategy

All automated tests are offline: `python -m pytest -q`. They need no credentials and never contact Google. API traffic is replayed with `googleapiclient.http.HttpMockSequence` against the client library's bundled discovery document, so request URLs, query parameters and bodies are the ones the real client would send.

## Unit tests

- Metadata validation: every limit at and beyond its boundary (title 100 characters and `<` `>`, description 5000 UTF-8 bytes after chapters are rendered, tag total 500 with YouTube's counting rule, chapter count, order, first-at-zero and 10-second minimum, caption formats, thumbnail type and size, playlist ID shape, language codes), required disclosures with no default, rejection of visibility and scheduling keys, all problems reported at once.
- Description rendering: chapter block format, hour formatting, refusal when the description already carries timestamps.
- Plan: request bodies, step list, quota estimate, duplicate state, warnings; visibility private by default and unlisted only when requested.
- Policy: status bodies with a disallowed visibility or a publish time are refused before any request.
- Source scan: the literal `public` and `publishAt` appear only in `policy.py`; no delete, update, channel, playlist, watermark or banner write calls exist anywhere under `src/`.
- CLI contract: every command, including usage errors and failures, prints exactly one JSON object; no output contains token-like strings.
- Auth and paths: token written 0600 inside a 0700 directory; config directory inside a git repository is refused; token status without printing secrets; refresh failure reported as needing `auth`.

## Integration tests against mocked HTTP

- Resumable upload: session initiation, multi-chunk upload with 308 responses, a 5xx mid-upload followed by a status query and continuation, retry exhaustion, a 401 answered by a token refresh.
- Cross-process resume from a ledger entry, including an expired session.
- Follow-up steps: thumbnail, captions, playlist insert; a failed step does not stop the others and is recorded so `--resume` retries only what is left.
- Duplicate guard: second `--execute` refused, `--resume` and `--new-upload` behavior.
- Readback: field comparison, missing fields reported as unverified rather than mismatched, caption and playlist presence.

## End-to-end

One real upload is run by hand after the one-time setup: a short clip uploaded with `--execute`, read back, and opened in a browser. The result is recorded in `docs/working.md`. There is no automated end-to-end test, because it would need a real channel and real credentials.

## What counts as verified

The offline suite passes on Python 3.10 and 3.12 in CI, and the manual end-to-end run produced a video whose readback has no mismatches.
