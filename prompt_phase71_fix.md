# Task: Phase 7.1 - make the assistant reliable (hybrid routing, number check, wording fix, model comparison)

Project: F:\bitcoin-ai-agent (BTC Signal Lab, Windows 11, Python 3.13, .venv). The current version is 0.7.0, with 364 tests passing. Ollama 0.40.13 is installed, with qwen2.5:3b and qwen3:4b.

This file is the complete task. Ignore any earlier, partial request.

## Why
In the Phase 7 live check, qwen2.5:3b answered only 4 of 8 questions fully correctly:
- it cited the wrong backtest run (#6 instead of #3);
- it said "no price data" when the price was in fact F1;
- it described the wrong backtest;
- it named the wrong publisher.

Answers took 58-82 s. The rule-based answers were correct, cited, and took about 2 s.

## Rules
- Back up the project, including data\*.sqlite3, first. Confirm 364 tests pass before you change anything.
- Do not change signal rules, weights, thresholds, `min_rr`, fixed constants or **ENGINE_VERSION** (changing it would make the current validation stale).
- No holdout run. Do not pull or install any model.
- Never weaken or delete tests. Never fabricate output.

## 1. Wording fix (no engine version change)
- In `signal_engine.validation_reason`, refer to "the validation backtest (run #N, <period> period, <entry mode> entry, engine X)" instead of "latest backtest". Include the period and entry mode in the stored validation record if they're missing there; old records without them must still work.
- In the assistant fact sheet:
  - one fact for the **validation run** (the run that decides the NO TRADE gate);
  - a separate fact for the **latest run**, if different, marked e.g. "Latest run #6: DESIGN period, pullback entry - research only, cannot validate", with its key numbers.
- Update the affected tests (only the wording), and add a test that both facts appear when they differ.

## 2. Hybrid routing
- Classify every question by intent first. These intents are always answered by the **rule templates** (instant, grounded, cited) and never sent to the model:
  - price, paper, backtest, validation, sources, why_no_trade, news, buy_sell_advice, prediction, injection/override attempts.
- The model is used only for:
  - open explanation questions (e.g. "explain the signal", "what does ADX mean here", "why is momentum bearish");
  - `/api/assistant/explain?ai=1`.
- In those cases the model gets the **rule-built grounded draft** plus the fact sheet. The instruction: "Rewrite the draft in plain language for a beginner. Do not add any number, date, name or claim that is not in the draft or the facts."
- The **Sources** list always comes from the draft's facts, never from the model's own citations.

## 3. Number and name check on every AI answer
- Extract every number from the AI text: integers, decimals, percentages, thousands with commas, and "run #N".
- Each number must match a number in the draft or fact sheet. Normalise commas and %, and allow rounding to the shown precision, e.g. 80,180.01 ≈ 80,180 ≈ 80.2k.
- Publisher names quoted in the answer must match a fact's publisher.
- **If any check fails:** withhold the AI text, return the rule draft with mode "rule" and the warning "AI answer withheld: it contained details not in the app data". Log what failed in `chat_messages.error`.
- Keep the existing advice/prediction filter as a second layer.

## 4. Speed and UX
- Default `OLLAMA_TIMEOUT_SECONDS` = 150.
- The loading text says "Thinking locally… usually 30-90 s on CPU" with a live seconds counter and a **Cancel** button (abort the fetch on the client).
- Instant rule answers show no long loader.
- Show mode labels: "Rule-based (instant)", "AI rewrite (local <model>)", "AI withheld - rule answer".

## 5. Model comparison (no downloads)
- Support qwen3 by sending `"think": false` in the /api/chat request when the model name starts with "qwen3". Also strip any `<think>…</think>` block from replies for every model.
- With the app running, ask the same 12 questions with `OLLAMA_MODEL=qwen2.5:3b` and then `qwen3:4b` (restart the app between):
  - the 8 Phase 7 questions;
  - "Explain the signal in simple words";
  - "Why is momentum bearish or bullish right now?";
  - "What does the ADX value mean here?";
  - "What would need to change for a BUY setup?"
- For each model, report: mode per answer, time, verbatim text for the 4 explanation questions, how many AI answers passed the number check vs were withheld, and any wrong content.
- Pick the default model by: most passed checks, then correctness, then speed. Set it in config.py and .env.example only if qwen3:4b is clearly better; otherwise keep qwen2.5:3b. Explain why.

## 6. Tests (fake Ollama, no network)
At least:
- intent routing (each factual intent never calls the model);
- explanation intents call the model with the draft;
- number check passes on a correct rewrite and fails on an invented number, a wrong run number and a wrong publisher;
- withheld → rule answer + warning + error logged;
- the Sources list comes from the draft;
- qwen3 requests include `think: false`; `<think>` blocks are stripped;
- the validation-run vs latest-run facts;
- the timeout default is 150;
- the validation_reason wording, including old records without period/entry mode.

## 7. Finish
- Clear the test conversation from the live DB (POST /api/assistant/clear) when done.
- APP_VERSION "0.7.1 (Phases 1-7)". Update the README: hybrid routing, number check, model choice, timings, test count.
- Run the full suite until it passes.
- Playwright screenshots of #assistant (1440x900 and 390x844) after one rule answer and one AI rewrite: data\phase71_assistant.png and data\phase71_assistant_mobile.png. Capture console errors.
- Stop the server.

## Report
1. Files changed, and tests before → after.
2. The new NO TRADE reason text, verbatim.
3. The model comparison table plus the 4 explanation answers per model, verbatim.
4. The 8 original questions on the chosen model: mode, time, verbatim answer, grade.
5. The chosen default model, and why.
6. Screenshots, console errors, and anything untested.

## Final summary (required)
- tests;
- grounding score (answers correct out of 12) per model;
- withheld count;
- chosen model;
- typical rule and AI answer times;
- open issues.
