# Module 7 — Presenter Cheat Sheet

Action-first run sheet. Each beat = **what you do** + a short *anchor phrase* to trigger the verbatim line.
Full wording lives in `MODULE7_TALK_TRACK.md`. Glance here, say it in your own voice, keep moving.

**Legend:** ▶ run · 👆 point · ⏸ pause/beat · ⌨ type · 💰 ISV money line · ⭐ hero line · ➡️ transition

---

## Pre-flight (before you go live)
```
cd agentic-ai
PYTHONPATH=. module7/.venv/bin/python seed_live_memory.py        # refresh data
module7/.venv/bin/python verify_live_connections.py              # expect 4x OK
```
**Run order:** 1 → 2 → 3 → 4 → 5 →(no pause)→ 6 → 7 → 8 → 9 · **~18–22 min**
**Running long?** 7 & 8 are *Flex* — summarize verbally in 30s each.
**Live-call sections:** 1, 3, 8, 9. If one stalls/errors → prefix `AGENT_MOCK_MEMORY=true` and re-run that section.
**Numbers to know cold:** 1024-dim (Titan v2) · 24h TTL · single-digit-ms reads · score 0.4–0.6 threshold · 0/3 tasks → healthy in 4 min · blast radius <50ms (sub-100ms at scale) · 6 memory tools · 3 ISVs.

---

## S1 · Why memory? ⏱1.5m · MUST
`▶ --section 1`
- 🎬 Open: *"Smart agent, uses tools, reasons well — but every new conversation starts from zero."*
- 👆 The response: *"It's telling us to check CloudWatch. No idea there was an incident, no root cause, no owner."*
- ⭐ *"This isn't a model problem — it's an architecture problem. The agent has no memory."*
- ➡️ *"And we'll fix it without you rewriting anything you've already built."*

## S2 · Add memory without rewriting ⏱2m · MUST
`▶ --section 2` — **Don't read the 6 tool names. Let them read; speak to the architecture.**
- 🎬 Open: *"This is the decision that makes memory portfolio-wide instead of a one-time project."*
- 👆 Backends box: *"Three backends, three memory types — Redis = working memory, MongoDB = long-term semantic, Neo4j = structural map."*
- 👆 Tools (let them read): *"Six memory tools added on top of the base agent's tools. You don't touch the base agent."*
- 👆 Guardrail: *"PII anonymized in the write path — emails, phones, AWS keys — on every store. Bedrock Guardrails layers in name detection."*
- ⭐ *"One composition pattern — the DAE. Memory is just another domain."*
- 🫱 Scope: *"Bedrock Agents has built-in memory; this is for custom LangGraph agents that need fine-grained control."*
- ➡️ Tease: *"By Section 7 you'll see why each ISV is irreplaceable."*

## S3 · Session memory — Redis ⏱2.5m · MUST · 2 live calls
`▶ --section 3`
- 🎬 Open: *"First memory problem is within one conversation. Redis solves it — not as a tool, as framework infrastructure."*
- 👆 Checkpointer line: *"Wired in at creation. The agent doesn't know Redis exists — the framework persists state every turn."*
- 👆 Turn 1: *"Recalls the incident and resolution — degraded, 0 of 3 tasks, healthy within 4 minutes."*
- 👆 Turn 2 ("it"): *"I just said 'it.' It resolved that because prior messages were restored from Redis before the LLM ran."*
- 👆 Checkpoint key: *"Here's the proof — we read the Redis checkpoint directly. The actual conversation is right there."*
- 💰 *"Why Redis Cloud: single-digit-ms reads, native TTL, perfect data fit. Free tier, no credit card."*
- ➡️ *"Redis handles 'now.' The real power is remembering across sessions."*

## S4 · Long-term semantic — MongoDB Atlas ⏱2m · MUST
`▶ --section 4`
- 🎬 Open: *"Yesterday's incident, last week's pattern — that's MongoDB Atlas."*
- 👆 Upsert / doc ID: *"Embedded by Titan v2 — 1024 dims. Doc ID is a content hash, so re-storing overwrites. No S3, no ingestion queue — queryable in single-digit ms."*
- 👆 Results (HEALTHY + DEGRADED): *"That score is cosine similarity. Query shared no keywords — pure semantic match. The agent has the incident AND the resolution."*
- 🫱 Calibrate: *"~0.57 is a solid match; 0.3 gets discarded. Set a 0.4–0.6 threshold — weak memories never reach the model, so it doesn't hallucinate from them."*
- 👆 Filtered query: *"Same query + severity=degraded pre-filter — Atlas filters server-side, drops the healthy record. At scale that distinction matters."*
- 💰 *"MongoDB Atlas on AWS Marketplace. M0 free tier handles this."*

## S5 · Build the graph — Neo4j ⏱1.5m · MUST
`▶ --section 5`
- 🎬 Open: *"Structural knowledge vector search can't represent — what depends on what, who owns what. That needs a graph."*
- 👆 Relationships: *"Six edges — notification-svc → api-gateway → auth-svc. Two teams, on purpose: blast radius crosses team boundaries."*
- 🫱 *"These are edges, not rows. Typed: DEPENDS_ON, OWNS, DEPLOYED_TO."*
- 🫱 MERGE: *"Run it ten times, still exactly six. Idempotent."*
- ⭐ *"A living map of your infrastructure, built as the agent works."*
- ➡️ **NO PAUSE — Enter past the Key Point, Enter again into S6:** *"Six relationships. notification-svc is degraded — who else is affected?"*

## S6 · Blast radius — Neo4j ⏱2m · MUST · strongest moment
`▶ --section 6` *(straight in, no re-intro)*
- 🎬 Open: *"The 2am scenario — what else is affected, and who do I wake up?"*
- 👆 Blast radius: *"Two hops: api-gateway and auth-svc. Chain: notification-svc → api-gateway → auth-svc."*
- 👆 Ownership: *"notifications-team owns the source, platform-team owns downstream — page both."*
- ⏸ Emphasis: *"That's one Cypher query. Under 50ms here, sub-100ms at hundreds of thousands of nodes."*
- ⭐ *"You cannot embed your way to blast radius — it's structural, not semantic."*
- 💰 *"A 2-minute investigation becomes a 2-second answer while the incident is live."*

## S7 · Why all three together ⏱2.5m · FLEX
`▶ --section 7`
- 🎬 Open: *"The evaluator's question — do I really need all three? Here's one that needs all three."*
- 👆 The question: *"'What happened last time notification-svc went degraded, and what depends on it?'"*
- 👆 Step 1 (Neo4j): *"Blast radius — structural. Vector search can't answer this."*
- 👆 Step 2 (Mongo episodic): *"What happened — content. The graph can't answer this."*
- 👆 Step 3 (Mongo procedural): *"How we fixed it before."*
- ⭐ *"The graph says where to look; MongoDB says what happened and how to fix it. Remove one and you can't answer."*

## S8 · Consolidation — gets smarter ⏱2m · FLEX · live call
`▶ --section 8`
- 🎬 Show 6-turn session first: *"This conversation happens dozens of times a week. Right now, when it ends, the knowledge disappears."*
- 👆 Extracted facts: *"These are the actual facts Claude extracted — not hardcoded. REDIS_URL required, fix+redeploy resolved it, plus the relationships it discovered."*
- 🫱 PII: *"Guardrail runs before the consolidation model ever sees the history."*
- ⭐ *"Next time, the agent recalls the fact — it doesn't re-run the investigation. Every session makes the next one faster."*
- 💰 *"After a month of incidents it knows your infra better than most engineers — no human intervention."*

## S9 · Full loop — the payoff ⏱3m · MUST · live call
`▶ --section 9`
- 🎬 Open: *"Same question as Section 1 — plus dependent services. Watch the difference."*
- 👆 THINK: *"It's reasoning — deciding which memory tool first."*
- 👆 ACT: *"recall_semantic_memory on MongoDB AND get_blast_radius on Neo4j — both ISVs, same reasoning step."*
- 👆 OBS: *"MongoDB returned incident + resolution. Neo4j returned api-gateway and auth-svc."*
- 👆 Final answer: *"Watch it pin down when it happened, the root cause, the blast radius, the teams to page."*
- ⭐ Split vs S1: *"Section 1: no context. Section 9: when it happened, root cause, blast radius, teams — same model, same question. Only difference is the MemoryDomainConfig."*
- ⚠️ Fallback (errors or >15s): *"Live Bedrock call — let me re-run in mock mode."* → `AGENT_MOCK_MEMORY=true PYTHONPATH=. module7/.venv/bin/python demos/module7_demo.py --section 9 --no-pause`

---

## Close (~1.5m)
- 🏗 Production shape: *"Redis shared cluster, MongoDB M10 one collection, Neo4j AuraDB Pro one graph — three managed services, all on AWS Marketplace, all on your existing AWS bill."*
- 🔁 Recap trio: *"Redis = session. MongoDB = long-term semantic. Neo4j = relationships."*
- 🧩 *"DAE composition adds all three to any agent you've already built — one `DomainAdapter.adapt()` call."*
- 📦 CTA: *"Code's in the repo. Mock mode runs in under 2 minutes. README has the 10-minute free-tier setup for all three."*
- 🔍 Prod note: *"LangSmith tracing is your debugging lifeline — every memory tool call, what it returned, and the recall scores."*

---

## If someone asks (one-line hooks → expand from talk track)
- **Bedrock KB instead of MongoDB?** *Write-to-read latency + upsert by content ID. KB = static docs; Atlas = dynamic agent-written memory.*
- **ElastiCache instead of Redis Cloud?** *Access model. ElastiCache = VPC setup first; Redis Cloud = public endpoint, free tier, seconds to start. Same checkpointer points at either.*
- **Neptune instead of Neo4j?** *Native vector indexes, official MCP server, free AuraDB to follow along. Pattern's identical — swap the connection string.*
- **Cost?** *Bedrock dominates; memory cuts re-reasoning. Redis free→per GB, Atlas M0 free (M10 ~$57/mo), Neo4j free→Pro ~$65/mo.*
- **Use with my Modules 1–6 agents?** *Yes — that's the point. `MemoryDomainConfig` is a `DomainConfig`. One adapt() call.*
- **When NOT to use it?** *Single-turn/stateless queries, or Bedrock Agents + doc Q&A. This is for ongoing, multi-session, structure-building agents.*
