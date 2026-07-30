# Model catalog check — 2026-07-30

## Unknown ids — BLOCKING

Present in the catalog, absent from the provider. Either a typo or a retirement. These are offered in the dashboard picker today.

- `openai` / `gpt-5.5`

## New models available

Not adopted automatically. Work the checklist per model, then edit `src/catalog.py` and `tests/fixtures/known_models.json` by hand.

21 new ids found; showing full checklists for the first 5.

### `anthropic` / `claude-fable-5`

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

### `anthropic` / `claude-fable-53`

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

### `anthropic` / `claude-mythos-5`

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

### `anthropic` / `claude-opus-4-1`

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

### `anthropic` / `claude-opus-4-5`

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

### Further candidates needing triage

- `anthropic` / `claude-opus-4-6`
- `anthropic` / `claude-opus-4-7`
- `anthropic` / `claude-opus-4-76`
- `anthropic` / `claude-opus-4-8`
- `anthropic` / `claude-opus-4-86`
- `anthropic` / `claude-opus-53`
- `anthropic` / `claude-sonnet-4-5`
- `anthropic` / `claude-sonnet-4-6`
- `anthropic` / `claude-sonnet-53`
- `openai` / `gpt-4`
- `openai` / `gpt-5`
- `openai` / `gpt-5.6`
- `openai` / `gpt-5.6-luna`
- `openai` / `gpt-5.6-sol`
- `openai` / `gpt-5.6-terra`
- `groq` / `llama-3.1-8b`

## Entries overdue for human review

- `openai` / `gpt-5.5` — last verified: never
- `groq` / `llama-3.3-70b` — last verified: never

## Fetch mechanism per provider

- `anthropic`: scrape
- `groq`: scrape
- `openai`: scrape
