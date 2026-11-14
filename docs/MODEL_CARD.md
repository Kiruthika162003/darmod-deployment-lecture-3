# Model card: Maple

Generated 2026-11-13 from commit `202adff`. Qualitative sections are written by people in `ops/card_human.json`.

## Version

- Prompt version in production: `v2.3`
- Base model: `gemini-3.1-flash-lite` (Google Gemini, served by Vertex AI)
- Temperature 0.5, thinking budget 0, at most 1024 output tokens

## Intended use

Answer questions about Vector Institute's programs, people and events in plain language, and take Vector store orders that a human approves. It is also the teaching example for how a deployed assistant is monitored. It is not for advice on funding applications, or on legal, medical or financial matters.

## Training data

Maple is not trained or fine-tuned. The base model is Google's Gemini, and its training data is described by Google. Maple adds a versioned system prompt and, for factual questions, Google Search results fetched at answer time.

## Evaluation

5 fixed questions about Vector Institute, each with a word the right answer must contain, asked of the running service on 2026-11-14. It is a small check, not a benchmark: it is enough to see what a change did, not to rank models.

| Prompt | Correct | Searched first | Median latency | Median output tokens |
|---|---|---|---|---|
| v2.3 | 5 of 5 | 5 of 5 | 3138 ms | 79 |
| v2.4 | 4 of 5 | 0 of 5 | 798 ms | 48 |

Per-group performance (by language, topic or user type) was not measured.

## Known limitations

- Maple is a prompted general model plus web search. When the search does not run (prompt v2.4, the search switch turned off, or a failed search) she can state wrong facts with confidence. On 14 November 2026 the v2.4 prompt gave a different answer from the searched one on who runs Vector Institute.
- English only. Other languages were not tested.
- Search results are not checked for accuracy before Maple uses them.
- A chat session lives in the memory of one copy of the app. A new copy starts with no history.

## Ethical considerations

- An order needs the person's name and email, and it ships only after an admin approves it. The model cannot place an order on its own.
- The logs hold a session id, lengths, tokens and version fields. They do not hold the text of questions or answers.
- Sessions are anonymous cookies. People should not paste personal data into the chat.
- A version that skips the search looks faster and cheaper on every infrastructure chart while its answers get worse, so quality is monitored separately.
