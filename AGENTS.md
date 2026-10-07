# Working on Live Interpreter

Read `README.md` for setup, `docs/architecture.md` for data flow and limits, and `DESIGN.md` before changing the interface.

## Rules

- Keep CloudFront as the only public entry point in the AWS deployment. Do not add a public ALB, NAT gateway or public task address.
- Deployments, resource deletion and credential changes require explicit user authorization. Do not interrupt an active session.
- Before restarting a local server, check session state and preserve any needed records. Uploaded PDFs and in-memory jobs do not survive a restart.
- Test with an isolated server and synthetic input. Never send test audio to an operator's running server.
- A committed caption appears once. Do not rewrite or rebroadcast previously displayed captions.
- Do not commit credentials, account-specific infrastructure settings, recordings or uploaded documents.
- Preserve source files when revising a user's document. Local `archive/` and `.local/` content is private historical material and must not be modified or included in releases.
- Use short Korean interface text without decorative punctuation. The product uses navy and orange; keep `DESIGN.md` as the source of design tokens.
- Follow the repository's intentional `AWS` prefix for current service names. Keep proper names unchanged.

## Code map

| File | Responsibility |
| --- | --- |
| `server.py` | App factory, route table and CLI; re-exports the names below for tools and tests |
| `settings.py` | Paths, limits, model IDs, timing and prices. Read `settings.DECKS` and `settings.WRITEUP_LIMIT` at call time; patch them there |
| `logtext.py` | Whether speech content goes into the log (`LOG_CONTENT`, `said`, `why`) |
| `captioning.py` | Text rules: language of a line, filler, repeats, subtitle checks, line breaking |
| `translation.py` | `Translator`: prompts, terminology, document context, model calls and fallbacks |
| `live.py` | `Hub`, `Session`, `Handler`: audio to Transcribe, commit once, translate, broadcast, record |
| `access.py` | Password, operator and caption links, login, `operator()` and `writable()` checks |
| `routes_live.py` | `/ws` and `/ws/rehearsal` (tests patch `routes_live.Session`) |
| `routes_materials.py` | PDF uploads, background rendering, analysis, corrections, slide images |
| `routes_session.py` | Session lifecycle and settings: context, speakers, terms, skills, usage |
| `routes_records.py` | Record downloads and write-up jobs |
| `security.py` | Browser origin and local host boundaries, safe access logging |
| `event.py`, `events/`, `glossary/` | Event configuration and terminology |
| `materials.py`, `deck_context.py` | PDF context, evidence, corrections and revisions |
| `pdf_worker.py`, `workloads.py` | Process isolation and background/AWS client concurrency |
| `postprocess/editorial.py` | Evidence-backed extraction, review and a concise document by subject |
| `postprocess/writeup-policy.md` | Shared editorial rules read by the app and agent skill |
| `.agents/skills/session-writeup/` | Repository skill for generating and revising session write-ups |
| `postprocess/process_recording.py` | Recording CLI, live write-up entry point and file conversion |
| `postprocess/html_document.py`, `document.css` | Passive standalone HTML and editorial punctuation |
| `postprocess/document.py` | Sandboxed Word conversion and literal transcript formatting |
| `static/studio.html`, `studio.css` | Studio markup and styles (tokens from `tokens.css`) |
| `static/studio.js` | Studio entry: sheets, session controls, finished-session view, start-up |
| `static/studio-state.js` | Shared state: `$`, `store`, `save`, `fetchApi`, `clientId`. Values more than one module reassigns live on `S` (`S.deck`, `S.page`, `S.sessionState`...) |
| `static/studio-setup.js`, `studio-audio.js`, `studio-settings.js` | Setup screen: session, slides, preview; audio input and sound check; speakers, terms, skills, context on Start |
| `static/studio-live.js`, `studio-log.js`, `studio-record.js` | Live view and control bar; log drawer; record and write-up |
| `static/studio-materials.js`, `studio-text.js` | Reference documents, evidence, rehearsal, upload polling; Markdown preview and microphone messages |
| `static/captions.html`, `captions.js`, `pcm-worklet.js` | Caption window and phone view; `CaptionClient` shared with the studio; audio capture |
| `infra/` | CDK deployment |
| `tools/prepare_release.py` | Public source package, content checks and optional fresh Git history |

The recording CLI with a configured agenda retains full translated turns. The studio and `--live` use the concise subject-based pipeline. Original transcript exports are never rewritten by AI.

For session write-ups, follow `.agents/skills/session-writeup/SKILL.md`. Maintain writing rules in `postprocess/writeup-policy.md`; do not duplicate them in the skill. The `skills/` directory contains live interpretation prompts, not agent skills.

## Checks

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python tests/test_commit.py
.venv/bin/python tests/test_studio_browser.py
.venv/bin/python tests/test_writeup_browser.py
.venv/bin/python tests/test_studio_controls_browser.py
.venv/bin/python tests/test_login_browser.py
```

Browser checks use an isolated server and system Chrome. Run `tools/design_tokens.sh` after editing `DESIGN.md`, then run browser checks after token generation finishes.

Update `CHANGELOG.md` for user-visible behavior. Keep task transcripts, local deployment addresses and per-commit work logs out of shared documentation.

Public releases come from `tools/prepare_release.py`. Do not push an existing development history containing private material. Do not publish to GitHub or send Slack messages without the user's authorization for that action.
