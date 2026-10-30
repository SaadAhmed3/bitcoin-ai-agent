# Security Audit

v0.9.1, 2026-10-30. Complements the existing [SECURITY.md](SECURITY.md) (Phase 9 threat model and controls).
Threat model: a single-user app on the user's own Windows PC; the main remote attacker is a malicious website in the
same browser (CSRF, DNS rebinding); secondary are hostile RSS content and a hostile local network.

## Findings

| Area | Status | Evidence / note |
|---|---|---|
| Bind address | ✔ | `resolve_bind`: 127.0.0.1 unless `APP_ALLOW_REMOTE=1`; a network bind prints a warning |
| Debug mode | ✔ | Off by default; refused on a non-loopback bind; reloader disabled |
| DNS rebinding | ✔ | Host allow-list (loopback plus `APP_ALLOWED_HOSTS`), port-checked |
| CSRF | ✔ | Writes need an allowed Origin, else an allowed Referer, else `X-Requested-With: BTCSignalLab`; no CORS is ever granted |
| Side effects on GET | ⚠ Low | GET `/api/paper` can execute a simulated stop/target exit (B16); GET `/api/signal` writes signal history. A cross-site `<img>` could trigger them, but they only act on rules the user set. Fixed by moving `check_levels` to the monitor (B6). |
| Input validation | ⚠ **Medium** | Paper, risk, alerts and settings are well validated (non-finite numbers rejected). **Signal engine settings are not range-checked** (B2): negative fees, slippage and `min_rr` are accepted and can distort validation. |
| Body size / JSON shape | ✔ | 64 KB limit; malformed or non-object JSON → 400 |
| Rate limiting | ✔ | Per-endpoint sliding windows on expensive or outbound routes |
| XSS | ✔ | Strict CSP (`script-src 'self'`, no inline script); 72 `innerHTML` sites go through `esc()`; links through `safeUrl()` (http/https or same-site path only); `javascript:`/`data:` RSS links dropped at ingestion; e2e test with a `<script>` headline. Residual risk: a future `innerHTML` without `esc()`. Consider a lint test that greps for unescaped template interpolation. |
| Path traversal | ✔ | `/docs/<name>` uses a fixed allow-list of 3 files; exports use a fixed dataset list; no user-controlled paths |
| File upload | n/a | None |
| Command injection | ✔ | No `subprocess`, `os.system`, `eval`, `exec` or `pickle` in app code |
| SQL injection | ✔ | Parameterised queries throughout `database/db.py` |
| CSV formula injection | ✔ | Export cells starting with `= + - @` are neutralised; numbers stay numbers |
| Secrets | ✔ | Only an optional CoinGecko Demo key and a contact e-mail, both in `.env` (git-ignored); the settings API reports only whether a key is set. No exchange keys exist; no trading permissions are possible. |
| Real-money trading | ✔ | No order endpoints to any exchange; `paper_orders.simulated` has `CHECK (simulated = 1)` |
| Ollama | ✔ | Loopback URL by default, with a warning otherwise; headlines are passed as data with an instruction-injection notice; AI output is filtered for advice, invented numbers and outlets |
| Dependencies | ✔ | Exact pins; `pip-audit -r requirements.txt` on 2026-10-30: **No known vulnerabilities found** |
| Error handling | ✔ | Unhandled errors → generic JSON 500; the trace stays in the local console |
| Logging | ⚠ Low | Console only; no rotating log file, so a crash cause is lost when the console closes. Recommend an optional `logs/app.log` with rotation (git-ignored). |
| Database integrity | ⚠ Medium | WAL plus a 10 s timeout. Paper account updates are not atomic read-modify-write (B1, reproduced). There is no backup/export of the whole database; recommend a "Back up database" button that uses SQLite's online backup API. |
| Resource use | ✔ / ⚠ | One backtest at a time; rate limits. Repeated `analyze()` per request (performance, not security). |
| Concurrency | ⚠ | Threaded Flask plus a background monitor; services share SQLite without application-level locks for paper (B1). |

## Recommended actions

1. **P0** — Per-field ranges for engine settings (B2), with tests.
2. **P0** — Lock plus a single-transaction read-modify-write for paper orders (B1), with a concurrency test.
3. **P2** — Remove side effects from GET `/api/paper` (B6/B16).
4. **P3** — Optional rotating log file; database backup button.
5. **P3** — A test that fails if `app.js` assigns `innerHTML` from a template literal containing `${` without
   `esc(`, `show(`, `plain(` or a known safe helper (keeps XSS hygiene from regressing).

No change to the local-first design is needed; no external service should be added.
