# Changelog

## Unreleased

- Interface language: the studio, the attendee caption page, the login page and server messages are available in 한국어, English and 繁體中文 (Taiwan). The language follows the browser at first; a selector in the studio header and on the attendee page changes it, and the choice is kept in the browser. Attendees see caption-language buttons for the session's own two languages instead of a fixed 한국어/English pair. Translations live in `static/locales/<code>.json` with the Korean text as the key; a new interface language is one more file there.
- Traditional Chinese (Taiwan): two new 통역 언어 choices, 영어와 중국어(대만 번체) and 한국어와 중국어(대만 번체). Transcribe listens for `zh-TW`, and Chinese captions use Traditional characters and Taiwan vocabulary (軟體, 資料, 雲端), polite 您/各位, full-width punctuation and Latin letters for product names; they are broken at Chinese commas and before clause words such as 然後 and 所以. Han characters without kana are read as Chinese only in a session that has Chinese, so Japanese sessions are unchanged. Chinese length is counted in characters. Amazon Translate falls back with `zh-TW`. Custom vocabularies and terminologies stay English and Korean only; glossary people and terms may carry an optional `zh` subtitle.
- Japanese: the setup screen has a new 통역 언어 choice, 영어와 한국어 (the default), 일본어와 한국어 or 영어와 일본어. Transcribe listens for the two chosen languages and each line is captioned in the other one. Japanese captions use polite です/ます speech, さん for names and Latin letters for product names, and are broken at Japanese commas and phrase ends. A session keeps its languages until it ends. Custom vocabularies and Amazon Translate terminologies stay English and Korean only, so Japanese speech is transcribed without them. Glossary people and terms may carry an optional `ja` subtitle.
- Online meetings: a new option, 내 목소리도 함께 통역, captures your own microphone together with the meeting tab or a virtual audio device such as BlackHole, so your side of the conversation is interpreted too. Meeting apps do not send your own voice back to you, so before this only the other side was captured. The microphone keeps browser echo cancellation on, its sound check is shown under it, and the studio asks you to use earphones so the other side is not interpreted twice.
- With that option the two inputs travel as separate stereo channels and Transcribe channel identification tells each line's side. The studio log marks lines 나 or 상대, the full record and its CSV (new 말한 쪽 column) keep the side, and write-ups use it to attribute statements. Both sides are captured when they talk over each other. Words the microphone picks up from the meeting are dropped as repeats. Captions on screen are unchanged, and single inputs are still sent as mono.
- Korean 클라우드 is no longer treated as a misrecognition of Claude. The shared glossary and the write-up terminology rules now say 클라우드 always means cloud computing, and English "cloud" becomes Claude only when the line is about a model or Anthropic. Before, cloud strategy, cloud governance and cloud task force names could come out as Claude in subtitles and write-ups.
- Write-ups report the length of the conversation instead of the last timestamp of the record. Silences longer than 10 minutes, such as an audio check long before the meeting, are not counted.
- Write-up rules: statements are attributed to the side the source identifies instead of "the vendor", and notes meant for attendees leave out personal career histories, unrelated third-party contract details and judgments about internal conflicts.
- Saved HTML write-ups keep their desktop layout when printed to PDF; the narrow-screen layout applies only on screens.

## 0.1.5

- Sign-in cookies carry a signed issue time and stop working after 3 days. The signing key is derived with scrypt, so a weak self-chosen password is no longer cheap to guess from an attendee cookie. Added `POST /logout`.
- After deploying this version the operator link changes once and everyone signs in again. Get the new link with `tools/operator_link.py`.
- Pages refuse framing. The studio sends a `script-src 'self'` policy, and HTTPS responses send HSTS. A non-loopback bind logs that a TLS-terminating proxy is required.
- PDF rendering runs with memory and CPU limits at lower priority. The PDF worker and Pandoc no longer inherit secrets or AWS credentials from the environment.
- Non-ASCII caption or operator keys are refused with 403 instead of a server error.
- Minimum versions: aiohttp 3.14.4, boto3 and botocore 1.43.108.
- Infra: new `region`, `vocabulary`, `existing_endpoints`, `rate_limit`, `alarm_email` and `monthly_budget_usd` settings. Alarms and a monthly budget are optional and off by default; set `alarm_email` to add them. Model IDs come from `settings.py`. All resources are tagged `project=live-interpreter`.
- Infra: `infra/local.example.json` is replaced by `local.new-vpc.example.json` and `local.existing-vpc.example.json`.
- The first deploy of this version replaces the running task. Deploy between sessions.
- Docs: a step-by-step deployment procedure in `docs/operations.md`, an English README, and instructions for deploying with Claude Code, Codex or Kiro (`CLAUDE.md`, `AGENTS.md`, `.kiro/steering/`).

## 0.1.4

- Fix: operators could not log in with the password on a deployed site. Browsers send `Origin: null` for the login form because of `Referrer-Policy: no-referrer`, and the origin check refused it. A null origin is now accepted only when the browser marks the request `Sec-Fetch-Site: same-origin`. Operator links were not affected.
- Add `tests/test_login_browser.py`, a real-browser login against an isolated server with a password.
- Fix: the deployed studio did not run. Module scripts are fetched with an `Origin` header, and CloudFront's `/static/*` behavior sent the ALB's name as `Host`, so the origin check refused `studio.js`. That behavior now forwards the viewer's `Host` (infra only).
- Deploying over 0.1.2 or earlier: invalidate `/static/*` once. CloudFront keeps a copy per compression, and copies stored before `no-cache` keep their old one-day lifetime. Here the brotli copy of `captions.js` was stale, so the rehearsal and Start sent the old start message and the server refused it.

## 0.1.3

- The full record keeps every committed utterance as heard, with its status (shown, skipped as filler or repeat, translation failed). Times are when speech was committed; the time a caption reached the screen is stored separately. Write-ups use shown and failed lines, not skipped ones.
- Write-ups start only for the ended session they were requested for, from the controlling window, one at a time. A timed-out write-up stops before its next model call, and a new one waits until the previous worker has ended. Preparing a new session clears earlier write-ups.
- PDF uploads return as soon as the file is received and render in the background; the studio polls `/api/uploads/{id}`. One upload is processed at a time. Large PDFs no longer depend on a single request staying open while rendering.

- Static files are sent with `Cache-Control: no-cache`, so browsers and CloudFront revalidate them and a deploy does not mix cached scripts with a new server.

- The speaker suggestion result note stays visible instead of being cleared at once, and the attendee and operator link buttons confirm the copy (both threw before).

Records from 0.1.2 have no status field and are read as shown. Clients posting `/api/writeup` must send the current `session_id`; clients posting `/api/deck` or `/api/references` must handle `202` and poll the upload status.

## 0.1.2

- Replace PyMuPDF with PDFium and Pillow; license project source under Apache-2.0 and document dependency licenses.
- Block document resource loading during Word conversion and preserve transcript markup as literal text.
- Enforce browser origin and local host checks with or without a password. External bind addresses now require a password.
- Omit queries and referrers from access logs, send a no-referrer policy and disable WAF request sampling.
- Require explicit public commit identities and verify every reachable public Git commit before sharing.

Upgrade Python dependencies with `tools/bootstrap.sh --dev`. An older environment may still contain PyMuPDF; a fresh environment is recommended. The request-boundary changes require a server restart after saving session records. WAF changes take effect only after an approved deployment.

## 0.1.1

- Add the repository's `session-writeup` agent skill for session notes and presentation summaries.
- Share the existing editorial policy between the application and the skill without changing its wording.
- Include the skill, synthetic examples and shared policy in public source packages.

## 0.1.0

Initial source release.

- English/Korean live interpretation with document context and configurable terminology.
- Private rehearsal, pause/resume, Q&A and a dedicated completed-session view.
- Passive HTML, Word and Markdown write-ups composed by subject, with separate review notes.
- Original transcript downloads in HTML, Word, CSV and Markdown.
- CDK deployment behind CloudFront with private application networking.

Records and uploaded materials are temporary. The application uses one server task and does not implement durable session recovery.
