# Email Gap Findings — What's in Gmail but Not in SoY

**Date:** 2026-04-11
**Purpose:** Inform the Phase 1.5 data hygiene work and the Phase 2 schema design by surfacing specific content that exists in Alex's Gmail but is missing from SoY's contacts / interactions / emails tables.
**Method:** SoY `v_discovery_candidates` view + targeted Gmail searches via MCP for entities the benchmark surfaced as gaps.
**Scope:** Top frequent correspondents + entities that failed the loci benchmark due to missing interaction history (Elana Dunkelman, James Andrews, CESD/Buchwald, Kerry Morrison).

---

## TL;DR

1. **The benchmark's most-cited sparse-data prompts (P1 Elana VO, R3 James Andrews email, P3 CESD/Buchwald follow-ups) all failed because SoY was missing real correspondence that exists in Gmail.** Not hypothetical sparseness — actual, recent, active email threads that SoY simply doesn't know about.
2. **Alex's real-world professional network is much denser than SoY represents.** A single VO industry email thread from late March had 8 CC'd colleagues, none of whom are in SoY contacts.
3. **Entire relationship categories are missing from the schema.** Employer/employee (BATL HR → Alex), GitHub org collaborator (Kerry's AloneinaBar → Alex), family (James Somerville → Alex), neighbor (Gerald Karaguni), service provider (vet, tax services) — the current contacts table is flat "individual vs company" and can't encode these.
4. **One specific finding is methodologically important**: the BATL HR "2025 T4 tips" thread the Qwen judge flagged as a hallucination in C3 A/B is actually in real Gmail. Real-world knowledge contamination crept into the benchmark — worth naming.

---

## The benchmark prompts that failed on data gaps, with the real data

### P1 — "I'm about to record VO with Elana — what should I have in mind?"

**SoY state at benchmark time:** 0 logged interactions with Elana, 0 decisions on elana-dunkelman-vo project, 0 transcripts. The gold answer called for an honest "limited data" response.

**Gmail reality:** **201 messages** between Alex and Elana Dunkelman (most recent: April 8, 2026). Active, substantive correspondence. Partial inventory of recent activity:

| Date | Subject | What's in it |
|---|---|---|
| 2026-04-05 → 08 | Website tweaks | 8-message thread about building/iterating Elana's VO site (elana-dunkelman-vo.vercel.app). Headers, Gallery anchor, tagline, bio, castability cards, credit reordering, studio copy. Version A feedback. |
| 2026-03-24 | Website stuff | Design direction brief — "warm, magnetic, effortlessly cool." Inviting/human/flirty style options. |
| 2026-03-25 | Animation and commercial | Elana sending demo reels for reference |
| 2026-03-26 | Wrong demo | "I totally sent you the wrong animation demo!!! Sorry!!!" |
| 2026-03-28 → 29 | Game Expo - March 28 & 29 | ACTRA Game Expo coordination. CC'd: Myles Dobson, Samy Osman, Cory Doran, Jon McLaren, Ivan Sherry, Craig Burnatowski, Anna Morreale, knightvisionfx@gmail.com |

**Also discovered in Elana's email signature** (would be captured as contact metadata):
- Principal agent: **Alison Little**
- Voice agent: **Jason Thomas**

**Interpretation:** P1 should be a *rich* prep prompt, not a sparse one. Alex is actively building Elana's VO site. A correct answer would say "you're mid-build on her website — the last thing shipped was [X], she asked for [Y], her style direction is warm/magnetic/effortlessly-cool, she's been signaling [Z] about her career direction through Game Expo participation." Every arm of the benchmark received "limited data" for this prompt and scored accordingly. The actual failure is **not loci's traversal** but **SoY's ingest pipeline not capturing Gmail content as interactions**.

### R3 — "Do I have James Andrews' current email?"

**SoY state:** Duplicate contacts (id 7 "James Andrews Talent Services" + id 9 "James Andrews VO"), same email. Zero interactions logged. The benchmark's R3 arm C on Mistral *hallucinated* Alex's own email (`j.alex.somerville@gmail.com`) and Alex's father's email (`jamescsomerville@gmail.com`) as "James Andrews' emails" because the duplicate contacts were present in the loci tree with no interactions to ground them.

**Gmail reality:** **201 messages** between Alex and James Andrews. A deep ongoing professional relationship. Highlights:

| Date | Subject | What it contains |
|---|---|---|
| 2026-03-23 | Your demo is ready! | Full demo production arc. James delivers the demo. Alex reacts ("This. Is. BRILLIANT. I SOUND SO GOOD!"). Mixing discussion about background tracks. James's "philosophy of demo production is to do broadcast quality work, indistinguishable from real ads." |
| 2026-03-23 | Re: Your demo is ready! (continued) | James advises Alex: "100% I think you should start pursuing some US agents." **This is directly relevant to the us-vo-agent-pursuit project** (project id 213, high priority, zero activity logged). |
| 2026-03-20 | Session notes | James reacts to Alex's new website (alexsomerville.com). "Sick website dude. I like everything about it. Just the whole vibe. Extremely cool." |

**Interpretation:** The R3 failure was a **data hygiene compound error** — SoY had duplicate contact records AND zero interaction history to disambiguate them. With proper interaction logging, even arm A would have easily answered "yes, james@jamesandrewsvo.com, confirmed in active correspondence." The benchmark was measuring loci's ability to work around a data problem, not loci's actual retrieval quality.

**Cross-reference to us-vo-agent-pursuit (project id 213):** This project is in SoY with "high priority, zero activity." In Gmail, James Andrews *explicitly told Alex on March 23* to start pursuing US agents. The project's rationale literally came from that conversation. None of this is in SoY as an interaction, a decision, or a note.

### P3 — "Have I actually followed up with anyone at CESD or Buchwald recently?"

**SoY state:** Contacts on file at CESD (Billy Collura, Marla Weber-Green, Christian Sparks) and Buchwald (Pamela Goldman, Katherine Ryan). Zero interactions logged with any of them.

**Gmail reality:** Direct correspondence with CESD/Buchwald is genuinely absent. BUT:
- **Tish Hicks at The VO Dojo** has been sending Alex promotional emails for The Nth Degree Career Catapult Intensive *featuring Billy Collura* (and other CESD-affiliated agents). Three such emails going back to October 2025. This is the vector through which Alex is "aware of but not in direct contact with" CESD agents.
- This is a weaker signal than Elana/James but it changes the framing: the honest answer to P3 shouldn't just be "no follow-ups" — it should be "no direct follow-ups; you've been receiving promotional content from The VO Dojo that name-drops CESD agents, which is probably what's shaping your awareness of them."

**Implication for schema:** There's no current concept of "adjacent correspondence" — email you receive *about* a contact but not *from* them. A full loci traversal should be able to surface "here's what you know about Billy Collura and here's where that knowledge comes from."

### C3 — "What's the relationship between my axe throwing job and the BATL Lane Command project?"

**Methodologically important finding.** The benchmark's Qwen judge flagged a hallucination in C3 arm A/B for mentioning "a BATL T4 discrepancy in the tax thread" as unsupported. But:

**Gmail reality:** Alex has an active BATL HR correspondence thread titled **"2025 T4 — tips income appears to be missing"** (April 2, 2026). It's real. The test subagent (Claude) appears to have had knowledge of this and leaked it into the answer, even though the benchmark's synthetic context didn't explicitly contain the thread.

This is a **real-world knowledge contamination** event — the test model had contextual awareness beyond what the arm's context provided, because Claude (running as the test subagent) might have been given some ambient grounding from the orchestrating session. Or more likely: the tax/T4/tips language was close enough to the context's BATL-adjacent content that Claude pattern-matched correctly by coincidence. Either way, **the "hallucination" Qwen flagged was factually correct in real life** — it was just unsupported by the synthetic arm context.

**What this means for future benchmark runs:** when we're evaluating loci on augmented/fake data, we need to be careful that the test model's pre-existing knowledge of the user's real situation doesn't leak in as "hallucinations." This is a classic leakage problem with personal-data benchmarks and the Qwen judge surfaced it inadvertently.

---

## New contacts that should be in SoY

Ranked by relevance / benchmark impact:

### High priority (missing in a way that affected benchmark scores)

| Name / Email | Why | Relationship type (schema gap) |
|---|---|---|
| **Alison Little** (from Elana's signature) | Principal agent for Elana. Industry contact. | "agent of" — currently no way to express |
| **Jason Thomas** (from Elana's signature) | Voice agent for Elana. Possibly also Alex's voice agent. | Same |
| **Shauna** at BATL HR (`hr@batlgrounds.com`) | Alex's actual employer HR. Source of employment agreement, T4, tips reconciliation. | "employer of" — no current schema concept |
| **Myles Dobson** (`contact@mylesdobson.com`) | ACTRA colleague. Coordinated Game Expo March 28-29. | "colleague in" — industry/professional network |
| **Jon McLaren** (`jonmclaren@me.com`) | ACTRA colleague. **Note:** already has a `wkspaces/jon-mclaren-vo` project folder on disk (I saw it earlier) but no contact record in SoY. |
| **Ivan Sherry** (`ivantoucan@gmail.com`) | ACTRA colleague. **Note:** already has a `wkspaces/ivan-sherry-site` project folder. Same gap pattern. |
| **Craig Burnatowski** (`craigburnatowski@gmail.com`) | ACTRA colleague. `wkspaces/craig-burnatowski-site` exists on disk. |
| **Samy Osman** (`sosman@runbox.com`) | ACTRA colleague from Game Expo thread. |
| **Cory Doran** (`corydoran@hotmail.com`) | ACTRA colleague, voice actor. |
| **Anna Morreale** (`annamorreale99@gmail.com`) | ACTRA colleague. |

### Medium priority (frequent correspondents, not directly benchmark-relevant but relevant to SoY's completeness)

| Name / Email | Emails | Context |
|---|---|---|
| **James Somerville** (`jamescsomerville@gmail.com`) | 20 in last period | Same last name. Almost certainly family (father). |
| **Meghan Hoople** at CavaVoices (`meghanhoople@cavavoices.org`) | 2 recent | VO-adjacent organization |
| **Tish Hicks** at The VO Dojo (`tishhicks@thevodojo.com`) | Ongoing promotional | Long-running marketing correspondence, name-drops CESD agents |

### Life admin (useful for "what's on my radar" prompts but not core benchmark)

| Name / Email | Context |
|---|---|
| **Gerald Karaguni** (`gerald.karaguni@gmail.com`) | Neighbor, active "Fence Project" coordination |
| Kew Beach Animal Hospital | Vet, "Soda & Odie" appointments |

---

## New interaction threads that should be logged

Counts are approximate based on search result metadata. Each counts as a single "thread" but most have multiple messages.

| Thread | Contact | Messages | Dates | Priority for SoY |
|---|---|---|---|---|
| Website tweaks | Elana Dunkelman | 8+ | Apr 5-8 | **HIGH** (active project, directly answers P1) |
| Website stuff | Elana Dunkelman | 1+ | Mar 24 | **HIGH** (project kickoff) |
| Animation and commercial | Elana Dunkelman | 1 | Mar 25 | Medium (reference material) |
| Wrong demo | Elana Dunkelman | 1 | Mar 26 | Low (correction) |
| Game Expo - March 28 & 29 | Elana + ACTRA group | 5+ | Mar 24-29 | **HIGH** (surfaces whole ACTRA network) |
| Your demo is ready! | James Andrews | 11+ | Mar 23 | **HIGH** (full demo production arc, decision material for agent pursuit) |
| Session notes | James Andrews | 3+ | Mar 20 | **HIGH** (Alex's VO website launch context) |
| 2025 T4 — tips income appears to be missing | BATL HR (Shauna) | 3+ | Mar 9 → Apr 2 | **HIGH** (employer relationship, affects financial/tax records) |
| BATL Tips 2025 | BATL HR | 1 | Mar 9 | Medium |
| Copy of My Employment Agreement | BATL HR | 2 | Mar 9 | **HIGH** (canonical employer doc) |
| Fence Project | Gerald Karaguni | 5+ | Mar 31 → Apr 8 | Low (life admin) |

---

## New relationship types that should exist in the schema

The current `contacts` table has `type` = individual or company, and an optional `company` column. That's the whole relationship model. Missing:

1. **Employer/employee relationships.** BATL HR isn't just a contact — they're Alex's employer. There's no way to encode "Alex works at BATL Axe Throwing" as a first-class relationship. This is the same gap that broke C3 (the axe-throwing ↔ BATL Lane Command prompt) at every model tier — the benchmark gold explicitly said the relationship is "implicit via user_profile, no FK link."

2. **Professional network / colleague.** ACTRA colleagues (Myles, Jon, Ivan, Craig, Samy, Cory, Anna) aren't clients, aren't company members, aren't vendors. They're peers. The schema has no "peer" or "colleague" concept.

3. **Agent-talent.** Alison Little and Jason Thomas are Elana's agents. When Alex thinks "Elana," he should also think "via her agents Alison and Jason." A traversal from Elana should reach her agents, but only through a "represents" edge that doesn't exist in the schema today.

4. **GitHub collaborator.** Kerry Morrison is the owner of `kmorebetter/better-software-of-you` upstream and has made Alex an owner of the `AloneinaBar` GitHub org. None of this is in SoY. The collaboration is real and ongoing but invisible to any loci walk.

5. **Family.** James Somerville (likely father) should be marked as family, distinct from professional contacts. When a prep prompt says "who should I consult about X," family has different gravity than a professional contact. The current schema can't distinguish.

6. **Neighbor / life admin.** Gerald Karaguni is a neighbor, not a professional contact. Would help the "what's fallen off my radar" prompt distinguish real action items from irrelevant ones.

7. **Adjacent correspondence.** Tish Hicks sends Alex emails *about* CESD agents. That's neither direct correspondence nor an agent-talent relationship — it's "here's someone mediating my awareness of a group I want to be in." The schema has no concept of mediated awareness.

---

## Implications for the data hygiene + schema work

Three specific things this report changes about the plan:

### 1. Email ingest needs to be the first data hygiene fix, not contact deduplication.

Originally I'd listed "merge the James Andrews duplicate" as the top data hygiene item. But merging two records that have **zero logged interactions** doesn't actually help — there's no real history to reconcile. The bigger fix is **ingesting the 201 James Andrews messages into the emails table and logging a few as contact_interactions**. With real interaction history, the duplicate resolves itself: both records point to the same active correspondence, and any sensible merge strategy keeps that correspondence unified.

### 2. The schema panel should be asked about relationship types as first-class entities.

The 7 missing relationship types above (employer, colleague, agent-talent, GitHub collaborator, family, neighbor, adjacent correspondence) can't be encoded in the current schema without either (a) polymorphic JSON columns that loci can't reliably parse, or (b) dedicated junction tables for each type. Sam Okafor's panel contribution should explicitly address which approach he'd recommend. This finding will arrive after his panel output but should be reconciled with it.

### 3. The fake parallel database idea gets stronger, not weaker.

I thought earlier that cleaning up the real SoY might be enough and the fake DB could be deferred. This email troll suggests otherwise: the real SoY is missing *so much structured information* that cleaning it in place would require weeks of manual ingest work. A fake parallel database **populated from real email content** would let us test loci against data that reflects Alex's actual life at the density the hypothesis deserves. The "realistic density" requirement is now concrete — match the 201 Elana messages, the 201 James Andrews messages, the ACTRA network, the BATL employer relationship.

An intermediate option worth considering: **write an ingest script that imports the ~1000 most relevant Gmail messages into SoY's existing `emails` table AND generates corresponding `contact_interactions` rows**, then re-run the benchmark on the augmented real data. That's 1-2 days of scripting. Fast, real, and the cleanest way to test "does loci work when the data actually reflects the user's life." This is a Phase 1.6 option that wasn't on the plan before.

---

## What I did NOT do in this pass

- **Didn't read full thread bodies.** Snippets were enough to establish existence and rough content. Reading bodies would burn context without adding signal.
- **Didn't search for every entity in SoY.** Focused on entities the benchmark surfaced as gaps. A complete audit would check every one of the 33 contacts against Gmail, which is a larger pass worth doing separately.
- **Didn't investigate Jessica Martin.** SoY already has good Jessica interaction coverage (recent email id 7 is the one the benchmark prompts reference). Assumed it's already well-represented.
- **Didn't attempt any auto-ingest.** Everything here is findings, not changes. No emails were written to SoY during this pass.

---

## Next steps (in order)

1. **Wait for the schema architecture panel** (`a50d77519be850921`) to return, then reconcile its proposals with the relationship-type gaps identified here.
2. **Write `docs/loci-data-hygiene.md`** as originally planned, but now informed by this report — the hygiene items are no longer "normalize existing fields" but "ingest email content into the interaction layer, then normalize."
3. **Decide: augment real SoY or build parallel fake?** With this report in hand, I lean toward a hybrid: **augment real SoY with a Gmail ingest script for the benchmark-relevant entities** (Phase 1.6), then re-run the benchmark, and THEN decide whether to build a fully fake parallel DB for testing more exotic schema proposals.
4. **Write `docs/loci-optimal-schema.md`** using the panel's output + this report as input. This is the deepest artifact of the phase and should be written last.
