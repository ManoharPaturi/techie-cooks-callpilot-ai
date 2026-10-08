# Contributing to CallPilot AI

Thanks for helping. CallPilot is built by **Techie Cooks** for Hacktoberfest Hack Day Coimbatore 2026.

## Ground rules
- **Everything stays on the Mac.** No cloud inference, no telemetry, no new network listeners without discussion.
- **No secrets in git.** Use `.env` (see `.env.example`); certificates and keys stay outside the repo.
- **Grounded output only.** Model output that reaches the user must cite transcript or note IDs and pass the guards in
  `backend/grounding.py` and `backend/assistant.py`.

## Workflow
1. Create a branch: `feat/...`, `fix/...`, `docs/...` or `chore/...`.
2. Make small, focused commits.
3. Run the checks:
   ```bash
   uvx ruff check .
   uv run pytest                      # model tests run when whisper.cpp and Ollama are up
   (cd frontend && npm run build)
   ```
4. Open a pull request using the template. CI must pass before merging.

## Code of conduct
See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
