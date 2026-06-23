# Module 7: Agent Memory Systems — Presenter Talk Track

**Series:** AI Agent Learning Series on AWS (AWS Marketplace)  
**Duration:** 15–20 minutes  
**Audience:** Developers, solutions architects, technical decision-makers  
**Demo command:** `PYTHONPATH=. module7/.venv/bin/python demos/module7_demo.py`  
**Single section:** `PYTHONPATH=. module7/.venv/bin/python demos/module7_demo.py --section N`

---

## Before You Present

### What this demo proves
Every prior module built agents that are stateless — each conversation starts from zero. Module 7 solves that by treating memory as a **domain adaptation layer** (the same pattern from Module 5) rather than a separate system. The agent gains four memory types: session (current conversation in Redis), episodic (past observations in MongoDB), procedural (resolution patterns in MongoDB), and relationship (dependencies and ownership in Neo4j). Three AWS Marketplace ISVs provide the backends: **Redis Cloud** for session memory, **MongoDB Atlas** for long-term semantic memory, and **Neo4j Aura** for graph traversal.

### Key technical facts to know cold
- **Redis Cloud** stores the full LangGraph checkpoint (message state) in Redis after every turn via a custom checkpointer implementation. The agent never calls Redis — the framework does it automatically. Low single-digit millisecond reads end-to-end, 24h TTL.
- **MongoDB Atlas** stores long-term semantic memories as documents with a 1024-dim embedding field. Atlas Vector Search (`$vectorSearch` aggregation stage) handles semantic recall with server-side metadata pre-filtering. Embeddings are produced by **Amazon Titan Text Embeddings V2** (1024 dimensions).
- **Neo4j Aura** is a managed graph database. Relationships are stored as directed edges. The query language is Cypher — declarative, readable, and something LLMs generate reliably. Variable-length path queries (`DEPENDS_ON*1..2`) find blast radius in one call.
- **Module 5's DomainAdapter** is the composition engine. `MemoryDomainConfig` extends `DomainConfig` — the same four levers (system prompt, knowledge corpus, tool scoping, guardrails) now apply to memory. No new framework, no new agent architecture.
- **Mock mode** (`AGENT_MOCK_MEMORY=true`) uses pre-built responses for all three ISV backends but keeps Bedrock LLM calls live.
- **Live mode** requires Redis Cloud free tier, MongoDB Atlas M0 free tier, and Neo4j AuraDB Free — all available on AWS Marketplace, all free with no credit card.

### Setup checklist (run before the session)
```bash
cd agentic-ai
PYTHONPATH=. module7/.venv/bin/python seed_live_memory.py   # refresh demo data (relative dates)
module7/.venv/bin/python verify_live_connections.py         # should show 4x OK
PYTHONPATH=. module7/.venv/bin/python demos/module7_demo.py --section 2  # dry run
```
**Run `seed_live_memory.py` before each session.** It seeds the sample records (the
incident, its resolution, the dependency graph) with timestamps relative to the current
date, so the sample data is always current. It is idempotent — safe to run repeatedly.

### Expected runtime
With live Bedrock calls and presenter narration, expect **18–22 minutes** for all 9 sections. If running long, Sections 7 and 8 are marked "Flex" — you can verbally summarize each in 30 seconds without running the demo command, bringing you back to ~16 minutes.

### If a live Bedrock call fails (Sections 1, 3, 8, 9)
Add `AGENT_MOCK_MEMORY=true` as a prefix and re-run that section — mock mode returns pre-built ISV responses but still makes live Bedrock calls. If Bedrock itself is throttling, use the `--mock` flag which mocks everything including LLM responses:
```bash
AGENT_MOCK_MEMORY=true PYTHONPATH=. module7/.venv/bin/python demos/module7_demo.py --section 9 --no-pause
# or full mock:
PYTHONPATH=. module7/.venv/bin/python demos/module7_demo.py --mock --section 9 --no-pause
```

### Terminal setup
Use a terminal width of at least 140 characters. On a 1080p screen shared via webinar, use 13–14pt monospace font. The rich panels are 140 chars wide — narrower terminals will wrap and look messy.

### What's on screen during the demo
The terminal shows colored output (if `rich` is installed) or plain text. Each section prints a header bar, the live output of the operation, and a "Key Point" callout at the end. You run each section with `--section N` or let all 9 run sequentially.

---

## Section-by-Section Talk Track

---

### Section 1: Why Agent Memory?
**Run:** `--section 1`  
**What happens:** A plain Claude Sonnet 4.6 call (no tools, no memory) is asked: *"Is the notification-svc issue from yesterday resolved?"* The model responds that it has no context about past events.  
**⏱ ~1.5 min | Priority: Must — sets up the problem**

**Customer question this answers:** *"Why do I need memory at all?"*

**Talk track:**

> "I want to start with a problem you've probably already hit. You've built an agent — it's smart, it uses tools, it reasons well. But every time you start a new conversation, it has no idea what happened before. Let me show you what that looks like."

*[Run section 1. Let the response print fully. Point to it.]*

> "The agent is telling us to check CloudWatch. It has no idea there was an incident yesterday. It doesn't know the root cause. It doesn't know which team owns the service. Every conversation starts from zero.
>
> This is the stateless agent problem. And it's not a model problem — Claude Sonnet 4.6 via Amazon Bedrock is one of the best reasoning models available. It's an architecture problem. The agent has no memory.
>
> That's what we're solving today. And we're going to solve it in a way that doesn't require you to rewrite anything you've already built."

---

### Section 2: How do I add memory without rewriting my existing agents?
**Run:** `--section 2`  
**What happens:** Prints the `MemoryDomainConfig` — domain name, 6 tool names, three ISV backends (Redis/MongoDB/Neo4j), Neo4j node types, and PII guardrail config. Lists the 4 DAE levers.  
**⏱ ~2 min | Priority: Must — architecture foundation**

**Customer question this answers:** *"I already have agents from Modules 1–6. Do I have to rebuild them to add memory?"*

**AWS value point:** The Domain Adaptation Engine from Module 5 means memory is a composable layer, not a replacement. One call to `DomainAdapter.adapt()` adds memory to any existing agent. Three ISV backends, one composition pattern.

**Presenter note — Section 2 is dense:** When the tool list prints on screen, don't read all 6 names aloud. Let the audience read it. Speak to the architecture box and the DAE levers instead.

**Talk track:**

> "Before we connect to any ISV, I want to show you the architectural decision we made. Because this is the part that determines whether memory is a one-time project or something you can apply across your entire agent portfolio."

*[Run section 2. Point to the box at the top showing the three backends — let the audience read it.]*

> "Three ISV backends, three memory types. Redis Cloud handles session memory — the current conversation, turn by turn, with a 24-hour TTL. MongoDB Atlas handles long-term semantic memory — episodic observations, procedural resolution patterns, consolidated facts — using vector search. Neo4j handles relationship memory — service dependencies, team ownership, blast radius.
>
> Each backend solves a different problem. Redis is fast and ephemeral — it's working memory. MongoDB is durable and semantic — it's long-term memory. Neo4j is structural — it's the map of your environment."

*[Point to the tool names list — let them read, don't enumerate.]*

> "Six memory tools get added to whatever tools the base agent already has. Your Module 1 infrastructure agent keeps all five of its AWS tools — it just gains six more for memory. You don't touch the base agent code."

*[Point to the PII guardrail config.]*

> "And notice the guardrail. Before anything is written to any backend, the agent anonymizes structured PII — email addresses, phone numbers, and AWS access keys — deterministically, in the write path. That runs on every store. For ML-based detection like person names, the pattern layers in Amazon Bedrock Guardrails as the production path. Either way, memory governance is built into the pattern from day one, not bolted on later."

*[Point to the 4 DAE levers.]*

> "The system prompt tells the agent its memory protocol. The knowledge corpus is MongoDB Atlas — dynamic, agent-written knowledge, not the static documents you'd put in Bedrock Knowledge Bases. Tool scoping adds the memory tools. Guardrails handle PII.
>
> The customer value here is that you don't need a new agent architecture for every capability you want to add. You have one composition pattern — the DAE — and you apply it. Memory is just another domain.
>
> One scope note: if you're using Amazon Bedrock Agents directly, conversation memory is built in. This pattern is for teams building custom agents with LangGraph who need fine-grained control over what gets stored and how it's retrieved — which is most production agent workloads we see.
>
> One more thing before we dive in — you might be thinking: three ISVs sounds complex. By Section 7 you'll see why each one is irreplaceable. But the short version is: Redis is your agent's short-term memory, MongoDB is its long-term memory, and Neo4j is its understanding of how things connect. Different data, different access patterns, different backends. One composition pattern to wire them all together."

---

### Section 3: How does my agent maintain context within a conversation?
**Run:** `--section 3`  
**What happens:** Creates a memory-augmented agent with a Redis-backed LangGraph checkpointer. Runs two turns of a real conversation — turn 2 uses a vague reference ("it") that only resolves if the agent has session context. Then reads the Redis checkpoint directly to show the actual persisted conversation.  
**⏱ ~2.5 min (two live Bedrock calls) | Priority: Must — first ISV (Redis)**

**Customer question this answers:** *"My agent forgets what was said two turns ago in the same conversation. How do I give it working memory?"*

**ISV value point — Redis Cloud:** Session continuity is handled at the framework level — a LangGraph checkpointer backed by Redis persists the full message state after every turn automatically. The agent never calls Redis. The framework does it. Low single-digit millisecond reads end-to-end, 24h TTL, no manual cleanup.

**Talk track:**

> "The first memory problem isn't long-term — it's within a single conversation. The agent answers your first question, you ask a follow-up, and it has no idea what you were just talking about. That's the session memory problem. Redis solves it — but not as a tool the agent calls. As framework infrastructure."

*[Run section 3. Point to the agent creation output showing "Session checkpointer: Redis Cloud".]*

> "Notice the checkpointer line. A LangGraph checkpointer backed by Redis is wired into the agent at creation time. The agent doesn't know Redis exists. The framework persists the full message state to Redis after every turn, automatically."

*[Point to Turn 1 — the agent answers about notification-svc.]*

> "Turn one. The agent recalls episodic memory, finds both the incident and the resolution, and gives us the full picture — the service was degraded with zero of three ECS tasks running, then resolved within four minutes of the fix."

*[Point to Turn 2 — the agent resolves 'it'.]*

> "Turn two. I asked 'what was the root cause of it?' — no context, just 'it.' The agent resolved that reference correctly because the prior messages were restored from Redis before the LLM was called. It didn't call a tool to get session context. The framework gave it the context automatically."

*[Point to the Redis checkpoint key in the output.]*

> "And here's the proof. We read the Redis checkpoint directly — outside the agent — and you can see the actual conversation stored there. Turn 1, turn 2, both responses. That's what gets restored on the next turn.
>
> Think about what happens without this. Every follow-up question requires you to re-state the full context. Redis eliminates that entirely.
>
> Why Redis Cloud over ElastiCache or DynamoDB? Three reasons. First, low single-digit millisecond reads end-to-end — the framework reads session state before every LLM call, so latency compounds. Second, native TTL — you set it once and Redis handles expiry. Third, the data structure fits perfectly — Redis is built for this kind of fast, ephemeral, key-value state.
>
> Redis Cloud is available on AWS Marketplace for consolidated billing. The free tier handles this demo with no credit card required."

*[Transition to Section 4.]*

> "Redis handles the 'now.' But the real power comes when the agent remembers across sessions — when yesterday's incident informs today's response. That's a different problem, and it needs a different backend."

---

### Section 4: How does my agent remember what happened across sessions?
**Run:** `--section 4`  
**What happens:** Stores a healthy/resolved episodic observation in MongoDB Atlas (the notification-svc outage resolution), then queries it back with semantic search — once without a filter, once with `severity=degraded`. The filter visibly removes the healthy record, leaving only the degraded one.  
**⏱ ~2 min | Priority: Must — second ISV (MongoDB)**

**Customer question this answers:** *"Redis gives me memory within a session. But what about across sessions — yesterday's incident, last week's resolution pattern?"*

**ISV value point — MongoDB Atlas:** Vector Search runs as a native aggregation stage — the filter is evaluated server-side before the similarity search. The agent writes directly at runtime with a single API call. No S3 bucket, no ingestion queue to wait for.

**Talk track:**

> "Redis handles the current conversation. But what about yesterday's incident? Last week's resolution pattern? That's long-term semantic memory — and that's MongoDB Atlas."

*[Run section 4. Point to the upsert result and the document ID.]*

> "The observation was embedded by Amazon Titan Text Embeddings V2 — a 1024-dimensional vector — and stored in MongoDB Atlas as a document. The document ID is a hash of the content, so storing the same observation twice overwrites the existing record. No duplicates.
>
> No S3 bucket, no ingestion queue to wait for. The agent called store_memory and the observation is queryable right now — single-digit milliseconds from write to read. For agent memory specifically — where an agent needs to write an observation and potentially recall it later in the same session — this direct-write-then-immediate-query pattern is what MongoDB Atlas is designed for."

*[Point to the results — you'll see HEALTHY and DEGRADED records with color coding.]*

> "That score is cosine similarity from Atlas Vector Search. The query was 'Have we seen container exit issues before?' — the stored observations are about ECS tasks and REDIS_URL. Those share no keywords with 'container exit issues,' but they're semantically related. That's the value of embedding-based search.
>
> Notice the results show both a HEALTHY record and a DEGRADED record — the agent has memory of both the incident AND the resolution.
>
> To calibrate what that score means: that 0.57 score is a solid semantic match — the query shares no keywords with the stored observation, but Titan Embeddings captured the semantic relationship. If you queried 'RDS failover configuration,' you'd get something in the 0.3 range — below the threshold, discarded. The closer to 1.0, the more confident the recall. In production you set a threshold, typically 0.4 to 0.6, and only memories above that threshold inform the response. The agent doesn't hallucinate from weak memories because it never sees them.
>
> Now look at the filtered result. Same query, but I added severity=degraded as a pre-filter. Atlas evaluates that filter before running the vector search — it removes the healthy record and returns only the degraded one. The filter removed 1 non-degraded record. At scale, when you have tens of thousands of observations across hundreds of services, that distinction matters.
>
> MongoDB Atlas is available on AWS Marketplace. The M0 free tier handles this demo. The Atlas Vector Search index we created earlier — that's what's powering this query."

---

### Section 5: How does my agent understand service dependencies?
**Run:** `--section 5`  
**What happens:** Creates six directed relationships in Neo4j: DEPENDS_ON, OWNS, DEPLOYED_TO edges between services, teams, and infrastructure. Two teams: `platform-team` owns api-gateway and auth-svc; `notifications-team` owns notification-svc.  
**⏱ ~1.5 min | Priority: Must — third ISV (Neo4j)**

**Customer question this answers:** *"My agent knows what happened to a service. But it doesn't know what else that service affects, or who owns it. How do I give it that structural knowledge?"*

**ISV value point — Neo4j:** Relationships are first-class data in a graph database. The agent writes them with the same `store_relationship` tool it uses for everything else. MERGE semantics make every write idempotent.

**Talk track:**

> "MongoDB Atlas tells the agent what happened. But there's a whole category of knowledge that vector search can't represent — structural knowledge. What depends on what. Who owns what. What's deployed where. For that, we need a graph."

*[Run section 5. Point to each relationship line as it prints.]*

> "Six relationships written to Neo4j. notification-svc depends on api-gateway. api-gateway depends on auth-svc. platform-team owns api-gateway and auth-svc. notifications-team owns notification-svc. api-gateway is deployed to ecs-cluster-prod.
>
> Notice we have two teams — platform-team and notifications-team. That's intentional. When notification-svc goes down, the blast radius crosses team boundaries. You'll see why that matters in the next section.
>
> These aren't rows in a table. They're edges in a graph — first-class relationships with types. DEPENDS_ON, OWNS, DEPLOYED_TO. The type matters because it determines what queries you can run.
>
> Notice MERGE semantics. Run this section ten times and you still have exactly six relationships. The agent can call `store_relationship` every time it discovers a dependency — from a Module 2 repo scan, from a Module 1 health check, from a conversation — and the graph stays clean.
>
> The customer value here is that the agent is building a living map of your infrastructure as it works. Every time it discovers a new dependency or ownership relationship, it writes it to Neo4j. Over time, the graph becomes the authoritative source of structural knowledge about your environment."

*[Do not pause here — after the Section 5 Key Point appears, hit Enter once to advance past it, then immediately hit Enter again to start Section 6. The narrative continues without a break.]*

> "OK, six relationships in the graph. Now watch what that gives us. notification-svc is degraded — who else is affected?"

---

### Section 6: How does my agent assess impact when something breaks?
**Run:** `--section 6` *(run immediately after Section 5 — no re-introduction needed)*  
**What happens:** Two Cypher queries: (1) `get_blast_radius("notification-svc", hops=2)` returns `api-gateway` and `auth-svc` as a dependency tree. (2) Team ownership query returns `notifications-team` owns notification-svc, `platform-team` owns api-gateway and auth-svc.  
**⏱ ~2 min | Priority: Must — strongest demo moment, do not skip**

**Customer question this answers:** *"When an incident happens, my agent needs to know immediately: what else is affected, and who do I page? How do I give it that capability?"*

**ISV value point — Neo4j:** Multi-hop graph traversal answers structural questions that are impossible with vector search. One Cypher query finds the full blast radius and team ownership simultaneously.

**Talk track:**

> "Here's the scenario every on-call engineer faces at 2am. Something is broken. The immediate questions are: what else is affected, and who do I wake up? Let's see how the agent answers those."

*[Run section 6 immediately — no header explanation needed.]*

> "notification-svc is degraded. Two hops through the DEPENDS_ON graph: api-gateway and auth-svc are in the blast radius. The dependency tree shows the chain: notification-svc → api-gateway → auth-svc.
>
> Now the ownership query. notifications-team owns notification-svc. platform-team owns api-gateway and auth-svc. So the agent knows: page both teams — notifications-team for the source of the incident, platform-team for the downstream services."

*[Pause here for emphasis.]*

> "That's one Cypher query. On our demo graph, under 50 milliseconds. In production with proper indexes on node labels, this pattern stays sub-100ms even at hundreds of thousands of nodes — that's what graph databases are optimized for. Try that with a vector database."

*[Beat.]*

> "I want to be direct about why this requires Neo4j and not a vector database. Vector similarity finds content that is semantically similar to a query. It cannot tell you that notification-svc has a directed dependency on api-gateway. That's a structural fact — a relationship — not a semantic similarity. You cannot embed your way to blast radius analysis.
>
> In a relational database, this is a recursive self-join — complex to write, expensive to run. In Neo4j, it's one line of Cypher: match all services reachable from this node via DEPENDS_ON edges up to two hops. The graph database was built for exactly this query pattern.
>
> The customer value is response time. The difference between a 2-minute investigation and a 2-second answer when an incident is active."

---

### Section 7: Why do all three ISVs matter together?
**Run:** `--section 7`  
**What happens:** Three-step query using `notification-svc` as the starting point — Neo4j finds its blast radius (api-gateway, auth-svc), MongoDB Atlas recalls episodic memories for those services, MongoDB Atlas recalls procedural resolution patterns.  
**⏱ ~2.5 min | Priority: Flex — can verbally summarize if running short on time**

**Customer question this answers:** *"These are three separate ISV subscriptions. Do I really need all of them?"*

**Combined value point:** The three systems are complementary. Redis handles working memory (this conversation). MongoDB handles content memory (what happened, how to fix it). Neo4j handles structural memory (what depends on what). Remove any one and the agent loses a capability.

**Talk track:**

> "This is the question I'd ask if I were evaluating this architecture: do I really need all three? Let me show you a question that requires all three to answer."

*[Run section 7. Walk through each step as it prints.]*

> "The question: 'What happened last time notification-svc went degraded, and what depends on it?'
>
> Step one is Neo4j. We ask the graph: what's the blast radius of notification-svc? The graph knows the dependency chain — api-gateway and auth-svc are downstream. That's structural knowledge. Vector search cannot answer this.
>
> Step two is MongoDB Atlas. We recall episodic memories — past observations — for the incident. The vector database finds what happened — that's content knowledge. The graph cannot answer this.
>
> Step three is MongoDB Atlas again. We recall procedural memories — how we resolved similar issues in the past. Again, content knowledge.
>
> The graph told us where to look. MongoDB Atlas told us what happened and how to fix it. Remove either system and you can't answer the question.
>
> The customer value here is that these three ISVs solve genuinely different problems. Redis is your agent's working memory — fast, ephemeral, conversation-scoped. MongoDB Atlas is your agent's long-term semantic memory — durable, searchable, cross-session. Neo4j is your agent's structural memory — relationships, dependencies, ownership. Remove any one and the agent loses a capability it can't recover with the other two."

---

### Section 8: How does the agent's knowledge improve over time?
**Run:** `--section 8`  
**What happens:** A 6-turn troubleshooting session is passed to the consolidation service. Claude extracts facts and relationships, stores facts in MongoDB Atlas's consolidated memory type, relationships in Neo4j. The actual extracted facts and relationships are shown on screen — not hardcoded samples.  
**⏱ ~2 min | Priority: Flex — can explain verbally without running if short on time**

**Customer question this answers:** *"The agent is useful today. But does it get smarter over time, or do I have to keep feeding it the same context?"*

**Combined value point:** Consolidation is the compounding mechanism. Each session produces durable knowledge that makes every future session faster. The value of both ISV subscriptions increases with usage.

**Talk track:**

> "Everything we've done so far has been storing individual observations — one health check, one relationship at a time. But the most valuable knowledge often emerges from a full troubleshooting session. Let me show you how that gets captured."

*[Show the mock session on screen before running.]*

> "Six turns. An engineer and the agent working through a notification-svc outage — diagnosing it, fixing it, confirming the resolution. This is a real conversation that happens dozens of times a week in any engineering organization. Right now, when it's over, that knowledge disappears."

*[Run section 8. Point to the stored count and the actual extracted facts.]*

> "The agent called Claude with a consolidation prompt: extract the durable facts and relationships from this conversation. What you're seeing on screen are the actual facts Claude extracted — not hardcoded examples. Claude identified that notification-svc requires REDIS_URL to start, that setting REDIS_URL and redeploying resolved the outage, and the relationships it discovered — like notification-svc depends on Redis. Those facts went into MongoDB Atlas's consolidated memory type. The relationships went into Neo4j as edges.
>
> One thing worth noting: the PII guardrail runs on the session history before it reaches the consolidation model — email addresses, phone numbers, and AWS access keys are anonymized deterministically in the write path before Claude ever sees them, and with Amazon Bedrock Guardrails enabled, ML-detected PII like names is anonymized too. The consolidation prompt is designed to extract facts and relationships, not personal information.
>
> The next time an engineer asks about notification-svc, the agent doesn't re-run the investigation. It recalls the consolidated fact directly. The session that just happened made every future session faster.
>
> This is the compounding value of the memory architecture. The longer your agents run, the more they know. Before this session, the agent knew a handful of facts about notification-svc. After consolidation, it knows the root cause, the fix, the team, and the dependency. After a month of incidents, it knows your infrastructure better than most engineers on the team — without any human intervention. That's the compounding effect.
>
> The value of all three ISV subscriptions increases with every session — not just the ones where something breaks, but every conversation where the agent learns something new about your environment."

---

### Section 9: Full Memory-Augmented Agent Loop
**Run:** `--section 9`  
**What happens:** The memory-augmented agent is invoked with: *"Is the notification-svc issue from yesterday resolved, and are any dependent services still affected?"* The Think→Act→Observe loop streams live — the agent calls `recall_semantic_memory` (MongoDB Atlas) and `get_blast_radius` (Neo4j), synthesizes a concise answer with when it happened, the root cause, and the blast radius.  
**⏱ ~3 min (live Bedrock call) | Priority: Must — the payoff, do not skip**

**⚠️ Presenter fallback — if Section 9 errors or takes >15 seconds:**
> "This is a live Bedrock call — let me re-run in mock mode to show you the flow."

Then run: `AGENT_MOCK_MEMORY=true PYTHONPATH=. module7/.venv/bin/python demos/module7_demo.py --section 9 --no-pause`

The mock response will show the same TAO loop structure with pre-built memory results.

**Talk track:**

> "Same question as Section 1 — with one extra clause asking about dependent services. Watch the difference."

*[Run section 9. Let the TAO loop stream — point to each phase as it appears.]*

*[Point to the THINK line.]*
> "The agent is reasoning — deciding which memory tool to call first."

*[Point to the ACT line.]*
> "It's calling recall_semantic_memory — querying MongoDB Atlas for past observations about notification-svc. And simultaneously calling get_blast_radius — checking Neo4j for dependent services. Both ISVs in the same reasoning step."

*[Point to the OBS lines.]*
> "MongoDB returned the incident and resolution records. Neo4j returned api-gateway and auth-svc as the blast radius."

*[Point to the final answer as it streams.]*

> "And now it's synthesizing. Watch it pin down when it happened. Watch it name the root cause. Watch it report the blast radius. Watch it name which teams to page."

*[After the response completes, point back to the Section 1 response if you have it visible.]*

> "Section 1: 'I have no context about a notification-svc issue.' Section 9: when it happened, root cause, blast radius, teams to page. Same model. Same question — with one extra clause asking about dependent services. The only difference is the MemoryDomainConfig — the six memory tools, the Redis checkpointer for session continuity, the system prompt, and the MongoDB and Neo4j backends.
>
> That's the value proposition. You already have the agents. You already have the AWS infrastructure. Redis Cloud, MongoDB Atlas, and Neo4j Aura on AWS Marketplace give you the memory layer that turns stateless tool-users into agents that learn from experience."

---

## Closing Points

> "Before I recap — let me tell you what this looks like in production.
>
> Redis Cloud on a shared cluster across your agent fleet — every agent writes session state to the same Redis, isolated by session key. MongoDB Atlas on a single M10 instance — one collection for all memory types, partitioned by memory_type and agent_id in the metadata. Neo4j on AuraDB Professional — one graph database that all agents write relationships to and all agents query from. The graph becomes your organization's shared structural memory.
>
> Total infrastructure: three managed services, all on AWS Marketplace, all billed through your existing AWS account. No servers to patch, no clusters to resize, no replication to configure."

> "To recap what we built today:
>
> Redis Cloud gives us session memory — turn-by-turn conversation history with a 24-hour TTL, low single-digit millisecond reads, and automatic expiry. Available on AWS Marketplace.
>
> MongoDB Atlas gives us long-term semantic memory — episodic observations, procedural resolution patterns, consolidated facts — with Atlas Vector Search and server-side metadata filtering. Available on AWS Marketplace.
>
> Neo4j Aura gives us relationship memory — service dependencies, team ownership, deployment targets — with multi-hop Cypher traversal for blast radius analysis. Also on AWS Marketplace.
>
> And the composition pattern — MemoryDomainConfig extending Module 5's DomainConfig — means you can add all three memory types to any agent you've already built. Module 1's infrastructure agent, Module 2's repo analyzer, Module 3's CDK generator. One call to DomainAdapter.adapt() and they all get memory.
>
> All three ISVs have free tiers — Redis Cloud free, MongoDB Atlas M0, Neo4j AuraDB Free — all available on AWS Marketplace, all free with no credit card. The README has the exact setup steps.
>
> The code is in the repo right now. Clone it, run `AGENT_MOCK_MEMORY=true PYTHONPATH=. python demos/module7_demo.py`, and you'll see everything I just showed you in under two minutes. When you're ready to go live, the README walks you through the free-tier sign-ups for all three ISVs — ten minutes of setup.
>
> One production note: when your agent has memory, debugging gets more interesting. LangSmith tracing — set up in Module 2 — shows you exactly which memory tools were called, what they returned, and the scores of the recalled memories. That trace is your debugging lifeline for memory-augmented agents."

---

## Handling Common Questions

**"Why not just use Bedrock Knowledge Bases for the semantic memory?"**
> "Bedrock KB does support metadata filtering and, as of 2025, has an API for programmatic document ingestion — so it's not a static-only system. The differentiator for agent memory is the write-to-read latency and the upsert semantics. MongoDB Atlas is queryable in single-digit milliseconds after a write — the agent can store an observation and recall it later in the same session. We also use a content-derived document ID so storing the same observation twice overwrites the existing record; Bedrock KB doesn't have that primitive. For static document RAG — PDFs, runbooks, wikis — Bedrock KB is the right choice. For dynamic agent-written memory where the agent needs to write and immediately recall, MongoDB Atlas fits the pattern better."

**"Why Redis Cloud instead of ElastiCache for session memory?"**
> "ElastiCache Serverless removes the provisioning complexity — that's not the differentiator. The difference is access model. ElastiCache lives in your VPC — you access it via a VPC endpoint, which means VPC configuration, security groups, and subnet selection before you write a line of code. Redis Cloud is a SaaS with a public endpoint and a free tier that requires zero infrastructure. For this demo and for development workloads, Redis Cloud lets you get started in seconds. For production, if you already have ElastiCache in your VPC, the LangGraph checkpointer takes a Redis connection string — point it at your ElastiCache endpoint and the architecture is identical. The operational model is your choice."

**"Why not use AWS Neptune instead of Neo4j?"**
> "Neptune Database supports openCypher, Gremlin, and SPARQL — so the query language isn't the differentiator. Three things make Neo4j the right choice for this demo. First, Neo4j has native vector indexes on nodes since version 5.11, so you can run hybrid graph+vector queries in a single Cypher call without a separate vector store. Second, there's an official Neo4j MCP server that lets agents access the graph as a tool — fitting the Module 4 MCP pattern directly. Third, Neo4j AuraDB Free gives everyone in this audience a free graph database to follow along with today — no VPC setup, no capacity planning. For production workloads where you're already running Neptune and need VPC isolation, the architectural pattern is identical — swap the connection string."

**"What does this cost in production?"**
> "The ISV backends are the smaller cost. The dominant cost is Amazon Bedrock — every agent reasoning call (Claude Sonnet 4.6) and every embedding for memory storage (Titan Text Embeddings V2) is priced per token or per invocation. The memory architecture actually reduces your total Bedrock spend over time — an agent that recalls a past resolution doesn't need to re-reason through the same problem from scratch. For the ISV backends: Redis Cloud free tier handles up to 30MB; beyond that, pricing is per GB/month. MongoDB Atlas M0 is free; the entry-level dedicated tier (M10) starts at approximately $57/month as of mid-2026 — check AWS Marketplace for current pricing. Neo4j AuraDB Free is free up to 200K nodes; Professional tier starts at approximately $65/month. All three are on AWS Marketplace for consolidated billing."

**"Can I use this with my existing Module 1–6 agents?"**
> "Yes, that's the point. `MemoryDomainConfig` is a `DomainConfig` subclass. You pass it to `DomainAdapter.adapt()` with your existing base model. The adapter adds the six memory tools alongside whatever tools your base agent already has, and wires in the Redis checkpointer for session continuity. No rewriting required."

**"The scores in Section 4 are around 0.55. Is that good?"**
> "With real Bedrock embeddings, 0.55 cosine similarity is a solid semantic match — the query and the stored observation are meaningfully related. In production you'd set a minimum score threshold (typically 0.4–0.6 depending on your use case) to filter out weak matches. The score is much higher than what you'd see with mock embeddings because Titan Embeddings v2 is doing real semantic encoding."

**"Where does my data live with these three ISVs?"**
> "All three support AWS regions — you choose the region at cluster creation. All three encrypt data at rest (AES-256) and in transit (TLS). For production deployments requiring network isolation, both MongoDB Atlas and Neo4j Aura support AWS PrivateLink — your data never traverses the public internet. Redis Cloud supports VPC peering and Private Service Connect on AWS. And because all three are on AWS Marketplace, procurement and billing flow through your existing AWS agreement, which simplifies the data processing addendum conversation with your legal team."

**"When shouldn't I use this pattern?"**
> "If your agent handles single-turn, stateless queries — 'what's the price of X?' or 'summarize this document' — you don't need memory. If you're using Amazon Bedrock Agents with built-in conversation memory and your use case is Q&A over documents, Bedrock Knowledge Bases handles that natively without this architecture. This pattern is for agents that have ongoing relationships with users, manage multi-session workflows, and need to build structural understanding of complex environments over time — infrastructure agents, incident response agents, developer productivity agents. The more sessions the agent runs, the more valuable the memory becomes."

**"I'm already using Amazon Bedrock Agents — do I need this?"**
> "If you're using Bedrock Agents directly, conversation memory is built in for session continuity — you don't need the Redis checkpointer for that. This architecture is for teams building custom agents with LangGraph or similar frameworks where you need fine-grained control over what gets stored, where it goes, and how it's retrieved. That's most production agent workloads we see — because the default memory in managed agent services is session-scoped and doesn't give you long-term semantic recall across sessions or graph-based reasoning about your environment."

**"How do I debug a memory-augmented agent in production?"**
> "When an agent has memory, debugging gets more interesting. If the agent gives a wrong answer, you need to know: was the memory it recalled correct? Was it stale? Did it retrieve the right memory but reason incorrectly? LangSmith tracing — which we set up in Module 2 — shows you exactly which tools were called and what they returned. For memory-augmented agents, that trace is your debugging lifeline. You can see the exact MongoDB query, the scores of the returned memories, and the Cypher query that ran against Neo4j — all in one trace."
