# CallPilot AI — Test report

2026-10-06 20:06 · arm64, 8 GB RAM, macOS 27.0.1 · ggml-base.en + qwen3:1.7b, all local.

- **Automated tests:** 126/126 passed, 0 failed, 0 known miss
- **Safety cases (real local Qwen):** 20/20 correct · false negatives 0 · false positives 0
- **End-to-end demo calls:** 4/4 as expected
- **Single Qwen safety decision:** median 2.1 s, p95 3.0 s

See `index.html` for full tables and screenshots; raw data in `demo/results.json` and `docs/report/pytest.xml`.
