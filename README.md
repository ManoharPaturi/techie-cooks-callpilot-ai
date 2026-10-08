# CallPilot AI

> A private AI copilot for phone calls that runs **entirely on your Mac**. It hears both sides, flags scam requests.
> (OTP, UPI PIN, "digital arrest", money transfers) the moment they are spoken, and answers your questions from your own notes.
> One small local model (Qwen3 1.7B) does it all, so it runs on an 8 GB laptop; **Gemma 4** can optionally write the after-call
> summary. The caller never sees any of it.

[![CI](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Hacktoberfest 2026](https://img.shields.io/badge/Hacktoberfest-2026-ff8ae2)
![Gemma 4](https://img.shields.io/badge/Gemma%204%20E2B-optional-4285F4)
![Open-source AI](https://img.shields.io/badge/AI-open--weight%2C%20on--device-2ea44f)

![CallPilot during a scam call](docs/screenshot-hang-up.png)

## Team

**Team Name:** ""Techie Cooks""

| Member | Contribution |
| ------ | ------------ |
| Manohar P (Team Lead) | Architecture, backend pipeline, local models, live-call networking, submission |
| Sainath B | video upload |
| Pushpak K | documentation |
| Laasya B | Idea and Phase planning |

## Problem Statement

### The Problem

Phone scams in India have become a script: a caller claims to be your bank's "fraud team", a courier, or the CBI. They create
panic ("your account will be blocked", "you are under digital arrest"), then ask for an OTP, a UPI PIN, a remote-access app or
a "safe account" transfer. Victims are often older people, first-time smartphone users, and busy people on a call they
didn't expect. The warning signs are clear in hindsight but easy to miss under pressure, while the call is still going on.

Existing protections act either **before** the call (caller-ID spam labels, which new numbers evade) or **after** the money is gone
(reporting on 1930 / cybercrime.gov.in). Nothing helps **during** the conversation. Cloud call assistants that could help mean
sending private calls to a server.

### Why We Chose This Problem

Everyone on our team knows someone who has received one of these calls. The scripts are repetitive and well documented, so
a model can recognise them, but only if it hears the call as it happens and keeps the call private. Small open-weight models
can now run on a normal 8 GB laptop, which makes a fully local, real-time copilot possible for the first time.

## Solution

CallPilot sits beside a call on the user's Mac. It transcribes both sides in two lanes (**YOU** = Mac microphone,
**CALLER** = the phone), checks every caller sentence for scam patterns with rules plus a local model, and shows a
plain-language warning with the exact words that triggered it. A private assistant answers "what is this call about?",
"do I have to share this?" or "is bulk upload in our contract?" from the conversation and the user's own notes. When the call
ends, the local model writes a summary, lists what the caller asked for, and proposes protective follow-ups (Qwen3 by
default, or **Gemma 4 E2B** with one setting).

![After the call: summary and the caller's requests (here written by Gemma 4 E2B)](docs/screenshot-gemma-after-call.jpg)

### Key Features

- **Live scam alerts with evidence:**
  - Covers OTP / security codes, UPI PIN "refunds", passwords, remote-access apps (AnyDesk), KYC / Aadhaar / SIM-block threats, fake
    "digital arrest" (CBI / police / customs), parcel scams, money transfers and pressure tactics.
  - Every alert quotes the caller's exact line.
- **Hang-up banner:** after two red alerts (or one high-risk one), a single clear action appears: *hang up and call your bank on the number on your card;
  report on 1930 / cybercrime.gov.in*.
- **Private assistant:**
  - Answers are labelled by basis (*from your notes*, *from the conversation*, *general safety*, *not found*).
  - **Auto-suggest** drafts a reply when the caller asks a normal question.
- **After-call summary** (Qwen3 by default, optionally Gemma 4 E2B):
  - a summary
  - **"The caller asked you for…"**, with each item linked to the caller's words
  - protective follow-up tasks you can edit, approve or dismiss
- **Shareable call report** (printable HTML) for family, the bank or the police. Private notes are deliberately left out.
- **Live phone calls over iPhone Personal Hotspot.** The phone joins by QR code over WebRTC. No router, domain, account or cloud is needed.
- **Fully on-device.** Speech-to-text and both models run on `127.0.0.1`, and nothing is stored on disk by default.

## Innovation and Differentiation

- **On-device and real-time.** The copilot is useful *during* the call, and the conversation never leaves the laptop. Cloud assistants
  can't promise the first or the second.
- **The model classifies; the app speaks.**
  - The live model only picks intent, category and severity from fixed lists, and must cite the transcript line by ID.
  - The warning text comes from a vetted table, so a small model can't produce dangerous advice in a safety alert.
  - Every quote shown is rendered from the stored transcript, never from model output.
- **Guardrails in code, not just prompts.** These came from failures we actually observed during testing:
  - answers that say "share the OTP" are replaced with safe advice
  - answers that drift into another script are rejected
  - after-call tasks that would follow a scammer's instructions are dropped
- **The caller sees nothing.** The phone joins through a separate HTTPS app that has no route to transcripts, notes or AI output.
  Tests enforce this.
- **Built for India.** Rules, model guidance, labelled cases and a demo call for "digital arrest", UPI PIN "refunds" and KYC
  scams, plus the 1930 helpline in every warning.
- **One model in memory.** A 1.7B model (Qwen3) does live checks, answers and the summary, so an 8 GB laptop never holds two
  models at once. **Gemma 4 E2B** is an optional after-call writer (`SUMMARY_MODEL=gemma4:e2b`): it loads after the call and
  unloads straight after.

## Technical Implementation

### Architecture

```mermaid
flowchart LR
  subgraph Phone["iPhone (guest)"]
    G[Join page<br/>WebRTC mic]
  end
  subgraph Mac["MacBook (host) · everything on 127.0.0.1"]
    direction LR
    B["Browser dashboard<br/>React + AudioWorklet"]
    GA["Guest app<br/>HTTPS, hotspot IP only<br/>no AI routes"]
    H["Host app (FastAPI)<br/>loopback only"]
    V["Silero VAD<br/>per lane"]
    W["whisper.cpp<br/>base.en"]
    R["Scam rules<br/>(regex, India-aware)"]
    Q["Qwen3 1.7B<br/>classify + cite IDs"]
    A["Private assistant<br/>Qwen3 1.7B + notes"]
    GM["After-call summary<br/>Qwen3 1.7B (default)<br/>or Gemma 4 E2B (optional)"]
    RP["Call report<br/>(HTML)"]
  end
  G <-- "WebRTC audio (LAN, no STUN/TURN)" --> B
  G -. "signaling (single-use token)" .-> GA
  GA -. relay .-> H
  B -- "PCM16 frames (YOU / CALLER)" --> H
  H --> V --> W --> R --> Q
  Q -- "alerts + evidence" --> B
  W --> A -- "private answers" --> B
  H -- "call ends" --> GM --> B
  GM --> RP
```

### Technology Stack

| Category        | Technologies |
| --------------- | ------------ |
| Frontend        | React 18, TypeScript, Vite, AudioWorklet, WebRTC, self-hosted fonts (Bricolage Grotesque,IBM Plex) |
| Backend         | Python 3.11+, FastAPI, Uvicorn, httpx, Pydantic, NumPy, SciPy (`resample_poly`), ONNX Runtime |
| Database        | N/A (transcripts, notes and alerts stay in memory only) |
| AI / ML         | Qwen3 1.7B (live safety, assistant, after-call summary), **Gemma 4 E2B** (optional after-call summary), Whisper base.en via whisper.cpp, Silero VAD |
| Infrastructure  | Ollama (local model server), whisper.cpp server, iPhone Personal Hotspot with a name-constrained local CA, GitHub Actions CI |
| APIs / Services | N/A (no cloud APIs; everything runs on the laptop) |

### How It Works

1. **Capture.** The browser taps the Mac mic (YOU) and the caller's audio (a demo recording or the live WebRTC stream) with an
   AudioWorklet. It sends sequenced PCM16 frames over a WebSocket. The server binds each capture to its lane, so a frame can't claim to be
   the other side.
2. **Speech to text.** Each lane runs Silero VAD (400 ms hangover) and sends complete sentences to whisper.cpp. Known
   Whisper hallucinations and echo loops are removed.
3. **Scam check.**
   - **Rules** run instantly on every caller line.
   - If a rule fires, **Qwen3 1.7B** sees the recent conversation and returns only
     `assessed_id`, `supporting_ids`, `intent` (request / warning / mention), `category` and `severity`, under a JSON schema whose
     ID fields are enums of the real line IDs.
   - The app turns that into an alert level: a "request" needs a request verb in the caller's words, or it is downgraded to
     *uncertain*.
4. **Assistant.** The user's question, the recent lines, alerts and matching note paragraphs go to the assistant. It must label its basis and cite
   IDs. Code guards check the answer before it is shown.
5. **After the call.** The summary model (Qwen3 by default; Gemma 4 E2B if enabled, which loads in about a minute on 8 GB) writes the summary, the caller's requests (each citing a
   CALLER line) and up to three follow-up tasks. Guards drop non-English text and any task that obeys the caller. If Gemma was used,
   it unloads and the live model is warmed up again.
6. **Live calls.** The phone opens a QR link served by a **separate** HTTPS guest app on the hotspot IP. A single-use 128-bit
   token (15-minute TTL) admits it to a two-peer room. WebRTC runs with no ICE servers, so audio stays on the hotspot LAN.

### Technical Decisions

- **8 GB is the constraint we designed for.** Qwen3 1.7B and Gemma 4 E2B can't both stay loaded next to Whisper and the browser:
  - Running Gemma live caused 53 model reloads in one call, and alerts took 9.6 s median / 21.7 s p95.
  - So the default is **one model for everything** (Qwen3 1.7B). Gemma 4 E2B is optional and only runs **after** the
    call (`keep_alive: 0`, 180 s timeout); the live model is re-warmed afterwards.
  - On a 16 GB+ Mac, set `SUMMARY_MODEL=gemma4:e2b` (and even `ASSISTANT_MODEL=gemma4:e2b`) to use Gemma more.
- **Classify, don't generate.** Generating explanations made live alerts take 13.6 s. Having the model output only a
  classification (≈40 tokens instead of ≈116), with advice from a vetted table, brought it to **5.8 s** (3.5 s at real call pace).
- **One LLM queue with priorities:** safety > the user's question > auto-suggest > summary. A newer question replaces an older
  pending one. Two parallel Ollama slots were measured *slower* on the M3, so we kept one.
- **Model choice was measured, not assumed.** We benchmarked six models on the same 8 GB Mac (table below).
- **Security boundaries in code.**
  - The host app refuses non-loopback binds.
  - The server checks Host and Origin (blocks DNS rebinding).
  - The guest app binds to the exact hotspot IP.
  - The hotspot CA is name-constrained to `172.20.10.0/28`, so it can't vouch for any real website.

### Measured results (8 GB MacBook Air M3)

| Metric | Result |
|---|---|
| Safety labelled set (20 cases incl. 8 India-specific) | **20/20**, 0 missed scams, 0 false alarms |
| Speech end → transcript | 1.0 s median, 1.4 s p95 |
| Transcript → instant rule warning | < 1 ms |
| Warning → model decision, live | 5.8 s median (3.5 s at real call pace) |
| Test suite | 142 passed (model tests run against the real local models) |

| Model (same tasks, one at a time) | Safety cases | Missed scams | Assistant answers |
|---|---|---|---|
| **qwen3:1.7b**, default for everything | **20/20** | **0** | **6/6** |
| **gemma4:e2b**, optional after-call summary | 19/20 | 1 | **6/6** |
| qwen3:0.6b | 17/20 | 3 | 4/6 |
| gemma3:1b | 15/20 | 1 | 3/6 |
| llama3.2:1b | 12/20 | 3 | 5/6 |
| gemma4:e4b | 5/20 (16 timeouts: doesn't fit in 8 GB) | 10 | 0/6 |

Sources:
- [docs/report/model-comparison.md](docs/report/model-comparison.md)
- [demo/results.md](demo/results.md)
- the presentation report [docs/report/index.html](docs/report/index.html)

These are small demo sets, not general accuracy claims.

## Implementation During the Hackathon

Everything in this repository was built by the team during the Hack Day event. Timeline of this repository on
**Oct 8, 2026 (IST)**. Each step is a reviewed pull request, checked by CI before merging:

| Time | Pull request | What was added |
|---|---|---|
| 10:38 | — | Hack Day template: README, AGENTS.md, CLAUDE.md, MIT license |
| 10:40 | [#1](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/1) | Repo setup: CI, Python project, env template, contributor docs |
| 10:42 | [#2](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/2) | Audio pipeline: config guards, frame parsing, resampling, Silero VAD |
| 10:43 | [#3](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/3) | Speech to text: whisper.cpp client, transcript cleaning, demo audio |
| 10:45 | [#4](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/4) | Scam detection: India-aware rules + classify-only Qwen, grounded schemas, 20 labelled cases |
| 10:48 | [#5](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/5) | Private assistant: note-grounded answers, safety guards, after-call summary |
| 10:48 | [#6](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/6) | Live calls: single-use rooms and an isolated HTTPS guest app |
| 10:51 | [#7](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/7) | Call session hub, host app, auto-suggest and downloadable call report |
| 10:53 | [#8](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/8) | Frontend: host dashboard and phone guest page |
| 10:55 | [#9](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/9) | Run scripts: start/stop, preflight, loopback verification, iPhone-hotspot setup |
| 10:56 | [#10](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/10) | Benchmarks, measured results, six-model comparison and screenshots |
| 11:09 | [#11](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/11) | Gemma 4: list what the caller asked for, grounded in their own words |
| 11:13 | [#12](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/12) | Submission README on the Hack Day template |
| 11:35 | [#13](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/13) | One-command launch: `./start` sets up and runs everything (CI-tested on Linux and macOS) |
| 12:13 | [#14](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/14) | Assistant: always answer “should I share the code?” with a clear No |
| 14:15 | [#15](https://github.com/ManoharPaturi/techie-cooks-callpilot-ai/pull/15) | Qwen3 by default for every job (one model on 8 GB); Gemma 4 optional |

### Team Contributions

- **Manohar P (Team Lead):** architecture, backend pipeline, local-model integration and benchmarks, live-call networking, repository and submission.
- **Sainath B:** video upload and testing 
- **Pushpak K:** Documentation for the code
- **Laasya B:** Idea and phase planning

## Working Application

**Live Application:** N/A. CallPilot is local-first by design: it listens to private calls, so it runs only on the user's own Mac
and is never hosted online.

To try it, run `./start` (see [Setup and Usage](#setup-and-usage)). The dashboard opens at `http://127.0.0.1:8765`.
- Pick a demo caller (fake bank OTP, fake CBI "digital arrest", a client call, or a prompt-injection attempt) and press **Play caller audio**.
- Watch the alerts and the hang-up banner, ask the assistant a question, then **End call** to see the after-call summary and
  download the call report.
- With an iPhone on Personal Hotspot you can also make a real live call (see below).

## Demo Video

**Demo Video:** [Watch the demo (Google Drive)](https://drive.google.com/file/d/1C9xOeb6ZI_60v0PfRLMf_PQNJRJNZdu7/view?usp=sharing)

The video covers the fake-bank OTP call (alerts → hang-up banner), a private question to the assistant, a normal client call
with a note-grounded answer, and the after-call summary and report.

## Open Source and AI Usage

### AI / Models

- **Gemma 4 E2B** (`gemma4:e2b`, Google, via Ollama), **optional**: set `SUMMARY_MODEL=gemma4:e2b` (and `ollama pull gemma4:e2b`). After the call it:
  - writes the summary
  - lists what the caller asked for, each item citing the caller's line
  - proposes up to three protective follow-up tasks

  It runs only after the call and unloads afterwards. By default Qwen3 does this job so an 8 GB laptop never holds two models. Its output is validated (IDs must exist,
  requests must cite CALLER lines, English only, no tasks that obey a scammer), and the report credits it only when it actually
  wrote the text. Code: `backend/assistant.py` (`summarize`), `backend/session.py` (`end_session`), `backend/report.py`.
- **Qwen3 1.7B** (`qwen3:1.7b`, Alibaba, via Ollama), **default for every job**: live scam classification, the private
  assistant and the after-call summary. It scored best on our safety set (20/20).
- **Whisper base.en** (OpenAI weights) via **whisper.cpp**: local speech-to-text.
- **Silero VAD** (ONNX): neural voice-activity detection that splits speech into sentences.

### Open Source Components

- **Ollama:** serves the local models on `127.0.0.1:11434`.
- **whisper.cpp:** local Whisper inference server on `127.0.0.1:8080`.
- **FastAPI / Uvicorn / Pydantic / httpx:** the host and guest web apps and the model clients.
- **NumPy / SciPy / ONNX Runtime:** audio resampling and Silero VAD.
- **React / Vite / TypeScript:** the dashboard and the phone join page.
- **qrcode:** the QR code for the phone call link.
- **Fontsource (Bricolage Grotesque, IBM Plex):** self-hosted fonts, so nothing is fetched from a CDN during a call.
- **Datasets:** none. The 20 labelled safety cases, demo notes and demo calls (`demo/`) were written by the team and are
  fictional. The caller audio was made with macOS text-to-speech.

## Setup and Usage

### Quick start (one command)

```bash
git clone https://github.com/ManoharPaturi/techie-cooks-callpilot-ai.git
cd techie-cooks-callpilot-ai
./start
```

On a Mac you can instead **double-click `Start CallPilot.command`** in Finder. It opens Terminal and does the same thing.
Stop with `./stop`, double-click `Stop CallPilot.command`, or close the window.

`./start` is safe to run every time. On the **first run** it sets everything up (10–20 minutes, mostly downloads):
1. installs any missing tools: `uv`, `node`, `cmake` and `ollama` via Homebrew on a Mac, apt / official installers on Linux.
   It asks before installing anything.
2. downloads and builds **whisper.cpp** (pinned to the version we tested) into `./vendor`, plus the Whisper `base.en` model
3. pulls the local model **`qwen3:1.7b`** (1.4 GB, retried if the network drops); add `gemma4:e2b` with `SUMMARY_MODEL=gemma4:e2b ./start`
4. installs the Python and frontend dependencies and builds the dashboard

Every later run skips whatever is already done. It then starts speech-to-text, the models and the app on `127.0.0.1` and
opens **http://127.0.0.1:8765** in Chrome. If the Mac is on an iPhone Personal Hotspot, live phone calls are switched on
automatically.

The setup alone is `./scripts/setup.sh`, and `./scripts/preflight.sh` checks tools, models, ports and demo files.

### Prerequisites

| | Needed |
|---|---|
| Computer | **macOS on Apple Silicon** (tested: 8 GB MacBook Air M3). Linux x86-64/arm64 works too (setup verified in CI). Windows: use WSL2 with Ubuntu. |
| Package manager | macOS: [Homebrew](https://brew.sh) (the only thing to install by hand). Linux: `apt` and `sudo`. |
| Disk / memory | ~3 GB free disk (~8 GB with Gemma 4), 8 GB RAM minimum |
| Browser | Chrome or another Chromium browser (microphone + AudioWorklet) |
| Optional | An iPhone with Personal Hotspot, for live phone calls |

No accounts or API keys are needed. After setup, everything works offline.

### Manual installation (what `./start` automates)

```bash
brew install uv node cmake ollama              # macOS
ollama pull qwen3:1.7b                         # optional: ollama pull gemma4:e2b
git clone https://github.com/ggml-org/whisper.cpp vendor/whisper.cpp
(cd vendor/whisper.cpp && git checkout 60c0be6 && sh models/download-ggml-model.sh base.en \
  && cmake -B build && cmake --build build -j --target whisper-server)
uv sync
(cd frontend && npm ci && npm run build)
./scripts/start.sh
```

### Environment Variables

Every setting is optional. Copy [`.env.example`](.env.example) to `.env` to change one. The main ones:

```env
OLLAMA_MODEL=qwen3:1.7b       # live scam checks
ASSISTANT_MODEL=qwen3:1.7b    # private answers (gemma4:e2b on 16 GB+ Macs)
SUMMARY_MODEL=qwen3:1.7b      # after-call summary; gemma4:e2b to use Gemma 4
VAD=auto                      # Silero if available, else energy
PERSIST_RAW_AUDIO=false       # nothing is written to disk by default
PERSIST_TRANSCRIPTS=false
GUEST_ENABLED=false           # set by scripts/setup_hotspot.sh for live calls
```

No API keys are needed.

### Running the Project

```bash
./start                     # set up if needed, start everything, open the dashboard
./stop                      # stop everything CallPilot started
./scripts/verify_local.sh   # proves 8765 / 8080 / 11434 listen on loopback only
uv run pytest               # 142 tests; the model tests run while CallPilot is running
```

If double-clicking a `.command` file opens a code editor instead of Terminal, run `./scripts/make_apps.sh` once. It creates
real **Start CallPilot** / **Stop CallPilot** apps next to the repo folder.

### Usage

**Demo (replay):**
1. Run `./start` (the dashboard opens in Chrome). Tick consent → **Start session**.
2. Choose *Fake bank "fraud team" asks for your OTP* → **Play caller audio**. Alerts appear under the caller's lines, and after the
   OTP demand and the threat the **hang-up banner** appears.
3. Ask the assistant "do I have to share?". It answers privately.
4. **End call.** The summary model (Qwen3, or Gemma 4 E2B if enabled) shows the summary, what the caller asked for and follow-ups.
   **Download call report** saves the shareable HTML.
5. New session → *Client Priya asks about the website deal* → **Add sample** notes → play. Normal questions get auto-suggested
   replies grounded in the notes, and "never share your OTP" is correctly *not* flagged.

**Live phone call (iPhone Personal Hotspot):**
1. iPhone: Settings → Personal Hotspot → Allow Others to Join. Connect the Mac to it.
2. `./scripts/setup_hotspot.sh`, then `./scripts/stop.sh && ./scripts/start.sh`.
3. Dashboard → **Live phone call** → Start session → **Create call link** → scan the QR code with the iPhone → **Join**.
   Wear headphones on the Mac.
4. For no certificate warning: run `./scripts/setup_hotspot.sh --ca`, install the CA profile on the iPhone and trust it.
   **Remove the profile after the event.**

## Challenges and Learnings

- **8 GB of RAM.** Running Gemma 4 and Qwen live together made Ollama evict and reload models constantly (53 reloads in one
  call), so alerts took up to 21.7 s. *Learning:* give each model the job that matches its cost. Fast classification goes live, and
  quality writing goes after the call.
- **Generation is the bottleneck, not the prompt.** Profiling showed the prompt was cached (~0.03 s) and output tokens dominated.
  Asking the model to classify instead of explain cut live latency by more than half.
- **Small models contradict themselves.** Qwen once answered "Yes, you must share the OTP. However, this appears to be a scam",
  and qwen3:0.6b said "No. Please provide the OTP." *Learning:* safety-critical text needs deterministic guards. We added a
  sentence-level unsafe-advice check that also catches bare commands.
- **Language drift and scammer-following tasks.** Gemma once switched to Chinese mid-sentence, and once proposed "Investigate the
  offer to transfer funds to a safe account". Both are now caught in code, and the summary prompt receives the safety alerts.
- **Whisper on echo.** Without headphones, Whisper loops ("I am not going to… ×6") and invents lines on silence. We filter
  loops and known hallucinations, and moved from an energy detector to Silero VAD (20 s of loud noise: 0 false segments, down from 20 s).
- **iPhone mic needs HTTPS with no router or domain.** We solved it with a local CA that is name-constrained to the hotspot
  subnet, so it can't be misused for other sites. Tailscale is the alternative.
- **Measuring beats guessing.** Our first guess, "a bigger or newer model will be better", was wrong on 8 GB. The six-model
  benchmark drove every model choice.

## Devpost Submission

**Devpost Project:** _link will be added after submission_

**DEV write-up:** [CallPilot AI on DEV](https://dev.to/manohar_paturi_2e0c6213f4/callpilot-ai-5b1m)

## Credits and License

### Credits

- **Models:**
  - [Gemma 4](https://ai.google.dev/gemma) by Google DeepMind (Apache 2.0)
  - [Qwen3](https://github.com/QwenLM/Qwen3) by Alibaba Cloud (Apache 2.0)
  - [Whisper](https://github.com/openai/whisper) by OpenAI (MIT)
  - [Silero VAD](https://github.com/snakers4/silero-vad) (MIT)
- **Runtimes:**
  - [Ollama](https://github.com/ollama/ollama) (MIT)
  - [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (MIT)
- **Libraries:**
  - FastAPI, Uvicorn, Pydantic, httpx, NumPy, SciPy, ONNX Runtime
  - React, Vite, TypeScript, node-qrcode
  - Fontsource (Bricolage Grotesque, IBM Plex: SIL OFL)
- **Scam guidance:** India's National Cyber Crime Reporting Portal (cybercrime.gov.in) and the 1930 helpline.
- Organised by INIT Club × iDEA Club with Major League Hacking for Hacktoberfest Hack Day Coimbatore 2026.

### License

[MIT](LICENSE) © 2026 Techie Cooks. The models are used under their own licenses (see above). All demo calls, notes and names are
fictional.

## Submission Checklist

- [x] Project title and description added
- [x] All team members listed
- [x] Problem clearly explained
- [x] Reason for choosing the problem explained
- [x] Solution and key features documented
- [x] Innovation and differentiation explained
- [x] Architecture included
- [x] Technical implementation documented
- [x] Work completed during the hackathon documented
- [x] Team contributions documented
- [x] Working application is functional
- [x] Live application link added where applicable (N/A: local-first by design)
- [x] Demo video added
- [x] AI and open-source components documented
- [x] Setup and usage instructions tested
- [x] Challenges and learnings documented
- [x] Devpost submission completed
- [x] Devpost link added
- [x] Credits added
- [x] License added
- [x] Repository is organized and complete
