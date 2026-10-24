# Write-up: known limitations and what I'd do with another week

## Known limitations
- **Free-tier model capacity.** Gemini's free tier allows about 500 requests a day per Flash-Lite model,
  and each question uses two calls. Latency is spiky: the median is 3.7 s, but some answers take 10-15 s
  despite hedged calls and fallbacks. A paid key or another provider fixes this with env vars only.
- **Keyword retrieval** depends on the router rewriting questions into good English keywords. It scores
  25/25 on my recall set, but wording the pages never use can still miss, and the bot then says it
  doesn't know.
- **Code checks cover prices, user-stated figures and doctor names.** Other facts (durations, inclusions)
  rely on the prompt and on citations. Labelled table cells make price-to-treatment mix-ups unlikely, but
  the check can't prove which row a number came from.
- **Source quality is inherited:** a price-list row with no treatment name, an unrendered template
  sentence (skipped), and a videos page that is mostly titles.
- **Serverless state:** on Vercel, unanswered-question and lead logs live in `/tmp` and the runtime log,
  and an admin refresh only updates the instance that runs it. Durable refreshes go through the weekly
  GitHub workflow.

## With another week
1. An evaluation set of 60-80 questions in CI (expected facts, pages, status), scored on every change.
2. Hybrid retrieval: multilingual embeddings next to BM25 with rank fusion, plus a small reranker.
3. Prices extracted into structured rows (page, program, amount, conditions), so cost questions and the
   guard work from facts rather than text.
4. Postgres or Supabase for leads, unanswered questions and answer ratings, with a small review dashboard.
5. Streaming replies and per-snapshot answer caching, so the first words appear in about a second.
6. Section-level change alerts when a refresh finds a price change, instead of a silent swap.
