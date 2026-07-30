# Model catalog check — 2026-07-30

## New models available

Not adopted automatically. Work the checklist per model, then edit `src/catalog.py` and `tests/fixtures/known_models.json` by hand.

### `openai` / `gpt-5.6`

- [ ] Id is well-formed and present in the provider's live list
- [ ] Price recorded (input / output per MTok)
- [ ] Auth path confirmed — API key vs OAuth
- [ ] Thinking default, and whether disabling it is accepted
- [ ] Assistant prefill supported
- [ ] Sampling parameters accepted
- [ ] Data-retention or residency requirement
- [ ] Context window and max output recorded
- [ ] Speed / cost class assigned
- [ ] Long-context pricing threshold and multipliers recorded
- [ ] Which providers serve it

## Entries overdue for human review

- `groq` / `llama-3.3-70b` — last verified: never

## Fetch mechanism per provider

- `anthropic`: scrape
- `groq`: scrape
- `openai`: scrape
