# Live Interpreter

[한국어](README.md) | **English**

Live English and Korean captions for talks and meetings. Live Interpreter reads your slides in advance to get names and terms right, and gives you a full transcript and a summary when the session ends.

It was beta-tested at a live event and has since been adapted for use at other events.

> Personal project. Not an official AWS or Anthropic product, and not affiliated with or endorsed by Amazon or Anthropic.

![Operator studio](docs/images/studio.png)

The interface is in Korean. Button names below are shown in Korean with an English translation.

## Features

- **Two-way live captions**: English to Korean and Korean to English. A caption usually appears 3 to 4 seconds after the speaker finishes a phrase.
- **Slide-aware translation**: One presentation PDF and up to four reference PDFs are read page by page, and the people, product names and terms in them are used during translation.
- **Same captions on every screen**: The presentation screen, a separate caption window, attendee phones (via QR code) and OBS get the same captions in the same order.
- **Audio input**: Microphone, USB audio, browser tab audio (online meetings) or a recording file
- **Private sound check**: Test interpretation without showing it to the audience or adding it to the record.
- **Records and write-ups**: Full transcript (HTML, Word, CSV, Markdown) and a talk summary or meeting notes (HTML, Word, Markdown)

## How a session works

| Step | Operator | Behind the scenes |
| --- | --- | --- |
| Prepare | Upload the presentation and reference PDFs, choose an audio input | Each page is read for a summary, people and terms. You can start before analysis finishes |
| Sound check | Say a few words into the real microphone | Captions appear only on the operator screen and are not recorded |
| Interpret | Press **통역 시작** (Start), open the caption window, attendee link or QR code from the share menu | Every screen gets the same captions in the same order |
| Break, Q&A | **일시정지** (Pause), **질의응답** (Q&A) | Pause keeps the record and context. Q&A uses the whole deck and recent speech as context |
| Finish | Download the transcript, press **정리하기** (Write up) | The original transcript is downloaded unchanged. The write-up takes a few minutes |
| Next session | **새 세션 준비** (New session) | Clears documents, speakers, session terms and records |

Do not share the operator link with attendees. It controls the session.

## How it works

These are the steps from speech to caption. Most of the time goes into waiting for transcription to finalize a phrase.

![How one caption line is made](docs/images/caption-pipeline.svg)

- **Transcription**: Amazon Transcribe streaming detects English and Korean automatically.
- **Translation**: Claude Sonnet on Amazon Bedrock. If a response is slow, Haiku runs in parallel, and Amazon Translate is the final fallback.
- **Fixed captions**: A caption is never changed once it is shown, so captions do not flicker or repeat.

Live interpretation, document analysis and write-ups run as separate paths in the server.

![Inside the caption server](docs/images/server-paths.svg)

Write-ups extract facts with their source line numbers, check them against the transcript, and organize them by subject. Decisions and follow-ups appear only when the transcript supports them; owners and deadlines are never invented. The original transcript is never rewritten by AI.

See [Architecture](docs/architecture.md) for details.

## Quick start

To run it on your own computer, you need your own AWS account and credentials. Downloading the source does not deploy anything to AWS or connect to anyone else's environment.

**Requirements**: Python 3.12, [uv](https://docs.astral.sh/uv/), an official [Pandoc](https://pandoc.org/) release, AWS credentials and Chrome

```bash
tools/bootstrap.sh                          # install dependencies
aws login                                   # sign in to AWS
.venv/bin/python setup_event.py --dry-run   # preview AWS vocabulary changes
.venv/bin/python setup_event.py             # create vocabulary and terminology
./start.sh --port 8083                      # start the server
```

Open `http://localhost:8083` and allow microphone access. To try it without creating a vocabulary, use `./start.sh --port 8083 --no-vocabulary`.

<details>
<summary>Options and security notes</summary>

- Main options: `--event`, `--profile`, `--region`, `--model`, `--engine translate`, `--no-vocabulary`
- Default model IDs are in `settings.py`. The models must be available in your account and Region.
- By default the server listens only on the loopback address. Listening on an external address such as `--host 0.0.0.0` requires `OPERATOR_PASSWORD`. Sign-in cookies work only over HTTPS, so put a TLS-terminating proxy in front.
- Do not put passwords or operator links in shared files.
- Some Linux distribution Pandoc packages do not support sandboxed Word conversion. The bootstrap script checks this.

</details>

## Event configuration

Copy `events/example.toml` to a new name and add speakers, expected questions and terms. The example is a fictional event. Shared terms in `events/_base.toml` and `glossary/` apply to every session.

```bash
.venv/bin/python setup_event.py example --dry-run
./start.sh --event example --no-vocabulary --port 8083
```

## Documents and records

- PDFs can be up to 40 MiB and 300 pages each. Save PowerPoint files as PDF before uploading.
- Reference documents are used only as translation context and never appear on attendee screens.
- Audio is not stored. Documents and records live only in server memory and disk, and may be lost on a new session or a server restart. Download what you need.

See [Operations](docs/operations.md) for data handling and limits.

## Deploying to AWS

The AWS CDK deployment creates the following. CloudFront is the only entry point from the internet, and the server runs in subnets with no internet route.

![AWS architecture](docs/images/aws-architecture.svg)

[Deployment and operations](docs/operations.md) has the step-by-step procedure. You can follow it yourself or hand it to an AI coding agent.

### Deploying with an AI coding agent

The repository includes an instruction file for each agent, and all of them follow the same [AGENTS.md](AGENTS.md) rules. The agent asks before every command that creates or deletes AWS resources, and never prints passwords or operator links.

| Agent | Reads | How to start |
| --- | --- | --- |
| [Claude Code](https://claude.com/claude-code) | `CLAUDE.md` (imports `AGENTS.md`) | Run `claude` in the repository folder |
| [Codex](https://developers.openai.com/codex) | `AGENTS.md` | Run `codex` in the repository folder |
| [Kiro](https://kiro.dev) | `.kiro/steering/live-interpreter.md` (includes `AGENTS.md`) | Open the repository folder in Kiro |

1. Clone the repository.

   ```bash
   git clone https://github.com/leeay0219/live-interpreter.git
   cd live-interpreter
   ```

2. Sign in with `aws login` in your own terminal. The agent cannot sign in for you.
3. Open the agent and ask, for example:

   ```text
   Deploy this app to my AWS account in the Seoul Region by following docs/operations.md.
   Ask me before every command that creates or changes AWS resources.
   ```

4. After deployment, run the command the agent gives you **in your own terminal, outside the agent** to get the operator password or link. Do not paste the password into the agent conversation.
5. After the event, ask the same agent to delete the stacks. It also lists the resources that remain afterwards.

The deployment costs money for as long as it exists. Check the cost table below first.

### Cost

| Item | Estimate | Notes |
| --- | --- | --- |
| While deployed | About $7 per day | More than half is VPC endpoints. It does not stop automatically when idle |
| Translating a 45-minute session | About $1 | Transcribe is billed separately by duration |
| Document analysis, write-ups | Scales with pages and transcript length | Re-analysis is billed again |

Actual cost depends on Region, model and usage. Between events, deleting the stack and redeploying before the next event costs less.

## Known limitations

- Speakers are not identified in real time. Captions do not show who is speaking.
- With Bedrock global inference profiles, requests may be processed outside the selected Region. If data must stay in one country, check model and inference profile support.
- The model can misinterpret the source. Source line numbers and the review file make write-ups traceable, but do not guarantee correctness.

## Learn more

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Data flow and structure |
| [docs/operations.md](docs/operations.md) | Deployment, operations and data handling |
| [DESIGN.md](DESIGN.md) | Interface design rules |
| [CHANGELOG.md](CHANGELOG.md) | Changes by version |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development, tests and contributing |

If you use an AI coding agent such as Codex, the repository's [session-writeup skill](.agents/skills/session-writeup/SKILL.md) can turn a session record into meeting notes. The app's **정리하기** (Write up) feature works without it.

## License

The project source is licensed under [Apache 2.0](LICENSE). Dependencies keep their own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md). Diagrams use AWS Architecture Icons.
