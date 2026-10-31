# Smart Queue evaluation

## Local deterministic baseline

The curated dataset contains **150 labeled messages** in `../data/labeled_questions.jsonl`: 72 question/request examples across six intent groups and 78 non-question examples, including commands, emotes, conversational statements, and one instruction-like message. Labels are assigned by the dataset author. The evaluator calls the production deterministic `detect_candidate` and `normalize_text` functions, then applies the same token-order-insensitive `SequenceMatcher` comparison and 0.78 threshold used by the cheap online grouping stage.

Run from the repository root:

```powershell
python scripts/evaluate_queue.py
```

Observed result on the current source and dataset:

```text
Messages: 150
Question detection: precision=0.923 recall=1.000 (TP=72, FP=6, FN=0)
Cheap fuzzy grouping purity=0.986 (72 labeled questions across 77 predicted clusters)
```

Question precision is TP/(TP+FP); recall is TP/(TP+FN). Grouping purity is the fraction of labeled detected questions in their predicted cluster's majority hand label. Six of 78 non-questions are false positives, and one of 72 possible group assignments is a cross-intent merge. The zero false-negative result reflects this small curated set and does not establish production recall.

## Limitations

- This is a small, deliberately varied development sample, not a random or held-out Twitch corpus. It is not representative of every game, language, dialect, emote, bot or audience.
- Labels and group boundaries are human judgments; short chat text can be ambiguous. Dataset creation and evaluation are not blinded.
- The grouping metric measures only the deterministic cheap matcher. It does **not** score Gemini's batched semantic grouping pass, poll detection, moderation, queue priority or end-to-end EventSub delivery.
- The greedy offline grouping approximates the online matcher, but does not reproduce database lifecycle, session state, moderation deletion, snoozing, or Gemini batching. It should not be interpreted as a production-quality estimate.
- No live Twitch messages or Gemini calls were used to produce these metrics. Real channel rehearsal remains necessary, and the replay tool is optional.

## Prompt-injection coverage

`../data/prompt_injection_messages.jsonl` contains instruction-like chat lines. The mocked test in `../backend/tests/test_interactions.py` confirms an injected line remains inside the explicit JSON untrusted-data delimiters, Gemini's mocked response is validated as `GroupingOutput`, no tools are supplied, no challenge is created, and the Twitch send path is not called. This verifies the local boundary and test fixture only; it is not a guarantee against every prompt injection.
