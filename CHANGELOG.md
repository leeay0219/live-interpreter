# Changelog

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
