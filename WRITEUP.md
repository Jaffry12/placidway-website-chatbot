# Write-up: known limitations and what I'd do with another week

## Known limitations
- **Free-tier model capacity.** Gemini's free tier allows about 500 requests a day per Flash-Lite model
  (two calls per question). Response time is usually 3-8 s, but the free tier sometimes stalls. The bot
  gives up on a model after 12 s and falls back to another one, so a rare answer can take 15-25 s.
  A paid key, or a provider such as Groq, makes this consistent with no code change (env vars only).
- **Keyword retrieval.** BM25 is a deliberate choice for 7 pages, but it depends on the router turning the
  question into good English keywords. A question phrased with words the pages never use can miss the
  right passage. The bot then says it doesn't know rather than guessing, but it is still a miss.
- **The price check covers money, not every fact.** Amounts are verified in code against the cited
  sources; other facts (durations, what's included) rely on the prompt and the citation requirement.
  The check also can't tell *which* row a number came from: an answer that wrongly called a $50
  consultation fee the "treatment price" would still pass if it cited that table.
- **Page quality is inherited.** Some source content is thin or inconsistent: one price-list row has no
  treatment name, one sentence contains an unrendered template (`=$priceRange?>`, skipped), and the
  videos page is mostly titles. The bot can only be as specific as the pages.
- **Serverless state.** On Vercel the unanswered-question and lead logs live in `/tmp` and the server
  log, not in a database, and an admin refresh only updates the instance that handles it. Durable
  refreshes go through the weekly GitHub workflow, which commits a new snapshot.
- **Conversation memory is client-side.** The browser sends recent turns with each message. That is
  simple and stateless, but a page reload starts a new conversation.

## With another week
1. **An evaluation set in CI:** 60-80 questions with expected facts, pages and statuses, scored on every
   change (answer accuracy, citation accuracy, false "I don't know" rate), so prompt or retrieval changes
   are measured rather than eyeballed.
2. **Hybrid retrieval:** add multilingual embeddings next to BM25 (merged with reciprocal-rank fusion),
   plus a small reranker. Keep BM25 for exact terms such as treatment names and prices.
3. **Structured price facts:** at ingestion, extract every price into rows (page, program, amount,
   currency, conditions). Cost and comparison questions would then be answered from that table, and the
   guard could check *which* fact a number came from, not just that the number exists.
4. **Persistence and an admin view:** Postgres or Supabase for leads, unanswered questions and answer
   ratings (thumbs up/down), and a small dashboard to review content gaps and push leads to PlacidWay's CRM.
5. **Streaming responses and caching:** stream tokens so the first words appear in about a second, and
   cache answers to common questions per snapshot version.
6. **Change detection per section:** diff refreshed pages section by section and alert someone when a
   price changes, instead of silently swapping the snapshot.
