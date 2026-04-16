# Seed Contact Audit — next_soy v1

**Date:** 2026-04-10
**Threshold:** ≥3 messages in last 60 days OR ≥5 messages in last 12 months
**Method:** Gmail MCP date-filtered search (`after:2025/04/11` for 12mo, narrower for volume retries)
**Companion to:** `email_gap_findings.md` (earlier sketch, now corrected)

---

## Headline

Of the 17 existing SoY contacts, **only 5 (29%) actually meet the correspondence threshold**. Eight of them — the talent-agency records (CESD, Atlas, DDO, ACM, Stewart, SBV, Buchwald, Innovative Artists) — have **zero correspondence** in the last 12 months. They were seeded aspirationally; no real data hangs off them.

Meanwhile, **six new-candidate contacts** clear the threshold decisively and deserve seat-at-the-table treatment in next_soy: **James Somerville (father)**, **Ivan Sherry** (VO coach), **BATL HR / Shauna**, **Gerald Karaguni** (neighbor), **Jon McLaren** (client), **Craig Burnatowski** (client).

The "ghost contact" problem matters for the loci layer: walking the graph from cold contacts yields nothing. Dropping or demoting them in next_soy removes noise that currently dilutes recall.

---

## Summary Table

| Contact | Status | ~12mo count | Threshold | Recommendation |
|---|---|---|---|---|
| Jessica Martin | existing | 18 | ✓ | **KEEP** — active client (Grow App) |
| Anna Lee | existing | 20 | ✓ | **KEEP** — accountant, handles consultant invoices |
| James Andrews (×2 dup) | existing | 45 | ✓ | **KEEP** (dedupe the two records) — VO demo producer |
| Elana Dunkelman | existing | 12+ | ✓ | **KEEP** — active website-build client + friend |
| Jackie Warden | existing | 201+ | ✓ | **KEEP** — primary talent agent, on-camera agency |
| ACTRA Toronto | existing | 201+ | volume-only | **DEMOTE** — newsletter blasts only, no two-way |
| CESD | existing | 0 | ✗ | **DROP** or mark inactive |
| Atlas Talent | existing | 0 | ✗ | **DROP** or mark inactive |
| DDO Artists | existing | 0 | ✗ | **DROP** or mark inactive |
| ACM Talent | existing | 0 | ✗ | **DROP** or mark inactive |
| Stewart Talent | existing | 0 | ✗ | **DROP** or mark inactive |
| SBV Talent | existing | 0 | ✗ | **DROP** or mark inactive |
| Buchwald | existing | 0 | ✗ | **DROP** or mark inactive |
| Innovative Artists | existing | 0 | ✗ | **DROP** or mark inactive |
| Julie Gudz (DDO) | existing | 0 | ✗ | **DROP** — no correspondence |
| Katherine Ryan (Buchwald) | existing | 0 | ✗ | **DROP** — no correspondence |
| Kerry Morrison (Demac) | existing | — (not queried, likely 0) | ? | Follow-up query |
| Take 3 / IDIOM / Avalon / DPN / VOX / AVO / Ritter | existing | — (no email) | n/a | Placeholder agency records |
| Billy Collura / Marla Weber-Green / Christian Sparks (CESD) | existing | — (no email) | n/a | Ghost sub-records |
| Heather Dame / Melanie Thomas / Pamela Goldman / Micaela Hicks | existing | — (no email) | n/a | Ghost sub-records |
| **James Somerville** (father) | **NEW** | 201+ | ✓✓ | **ADD** — daily contact, advisor/family |
| **Ivan Sherry** | **NEW** | 201+ | ✓✓ | **ADD** — VO coach, client (website build) |
| **BATL HR / Shauna** | **NEW** | 8 | ✓ | **ADD** — employer |
| **Gerald Karaguni** | **NEW** | 9 | ✓ | **ADD** — neighbor (fence project) |
| **Jon McLaren** | **NEW** | ~3 direct + active site project | ✓ (marginal) | **ADD** — client (jon-mclaren-vo build) |
| **Craig Burnatowski** | **NEW** | ~2 direct + active site project | marginal | **ADD** — ACTRA peer + site client |
| Meghan Hoople (CAVA) | NEW | 2 | ✗ | Skip — promotional only |
| Tish Hicks (VO Dojo) | NEW | 201+ | volume-only | **DEMOTE** — newsletter blasts, not conversation |
| Myles Dobson | NEW | ~3 unique | ✗ (group-only) | Skip-as-contact; keep as ACTRA-group member |
| Samy Osman | NEW | 0 direct | ✗ | Skip — group-thread only |
| Cory Doran | NEW | 0 direct | ✗ | Skip — group-thread only |
| Anna Morreale | NEW | 0 direct | ✗ | Skip — group-thread only |
| Alison Little | NEW | 0 direct | ✗ | Skip — metadata only (Elana's principal agent) |
| Jason Thomas | NEW | 0 direct | ✗ | Skip — metadata only (Elana's VO agent) |

---

## Existing SoY Contacts — Details

### Qualifying (5)

**Jessica Martin** — `msjessmartin@outlook.com`, 18 messages in 12mo. Active Grow App client correspondence; planning/feedback threads.

**Anna Lee** — `awlee100@rogers.com`, 20 messages in 12mo. Accountant; regularly handles consultant invoices; frequently cc'd alongside James Somerville on family/financial threads.

**James Andrews** — `james@jamesandrewsvo.com`, 45 messages in 12mo. VO demo producer. Demo production arc Feb–Mar 2026 ("Your demo is ready!" Mar 23; "Session notes" Mar 20; "Checking in" Feb 4–9; earlier "Session Notes" Feb 26–Mar 4). Mention of A&W booking; father-in-law passed away Feb 16. **Note:** exists twice in SoY (id 7 and id 9) — dedupe needed.

**Elana Dunkelman** — `elanadunkelman@gmail.com`, 12+ messages in 12mo. Active website-build client + friend. Threads: Website tweaks Apr 5–8, Website stuff Mar 24, Animation and commercial Mar 25, Wrong demo Mar 26, Game Expo volunteer coordination Mar 23–29, older Aug 2025 Zoom link. Her email signature exposes agents "Alison Little" and "Jason Thomas" — those belong as *attributes* of Elana, not as first-class contacts.

**Jackie Warden** — `jackie@wardentalent.com`, 201+ messages in Jan–Apr 2026 alone (extrapolating: several hundred/year). Primary talent agent. Mix of: audition packets, casting callbacks, invoice cycles (PW26-0108/0118/0128 A&W pattern), Warden Talent newsletters, ACTRA memos. Family emergency Feb 10 2026. Headshot coordination Feb 4. **This is the densest, most structurally rich correspondent in Alex's inbox** — major loci seed.

### Demote (1)

**ACTRA Toronto** — `info@actratoronto.com`, 201+ messages. All newsletter blasts; zero two-way. Keep as an orientation reference (union membership), but store as a low-weight/broadcast-only contact so the loci layer doesn't waste budget expanding into it.

### Cold contacts — drop or mark inactive (8+)

All eight talent-agency records returned **0 messages** in a 12-month window when queried at the domain level (`from:cesdtalent.com`, `from:atlastalent.com`, etc.):

- CESD / Billy Collura / Marla Weber-Green / Christian Sparks
- Atlas Talent / Heather Dame
- DDO Artists / Julie Gudz
- ACM Talent / Melanie Thomas
- Stewart Talent
- SBV Talent
- Buchwald / Katherine Ryan / Pamela Goldman
- Innovative Artists

These are aspirational/research records, not correspondence partners. In next_soy I'd either:

- **(a)** drop them entirely (cleanest); or
- **(b)** move them to a `prospects` table separate from `contacts` so the loci layer never walks them as seed data.

Option (b) preserves the research value (these are agencies Alex is considering for future representation) while removing them from the primary graph. The schema panel already contemplated the `contacts` ↔ `entity_edges` split — extending that to a prospects table is a small addition.

Agencies without emails (Take 3, IDIOM, Avalon, DPN, VOX, AVO, Ritter) are the same story — prospective reference data, not contacts.

---

## New Candidates — Details

### Qualifying (6)

**James Somerville** (father) — `jamescsomerville@gmail.com`, 201+ messages even in a 2-month window. Father, living in Portugal. Daily content-share volume: BBC/CNN/Globe article forwards (AI, business, taxes), personal (cottage, creatine, epilepsy/DTI research), business advisor (BATL app product development thread, Potential partners list, vibe coding, "Taxes" thread re: instalments). Often cc'd with Cameron Somerville (brother), Anna Lee (accountant), Chris Graham, Ainslie Roberts. **Highest-volume personal correspondent in inbox.** Add as `family` + `advisor` roles.

**Ivan Sherry** — `ivantoucan@yahoo.ca` (note: the `@gmail.com` variant in CC fields is a typo/alias). 201+ messages in 12mo. Active relationship: VO coaching ("Hiiiiii (a favour to ask)" thread Mar 1–4 about British/midlands accent for a big game audition, 8+ turns), coaching testimonial Alex wrote for him Mar 20, Website thread Apr 2–6 ("working on sites for Elana and Craig" — Alex is building his site too). Squarespace form submissions for his teaching forwarded to Alex. **Coach + mentor + current client (site build).** Add as `coach` + `client`.

**BATL HR / Shauna** — `hr@batlgrounds.com`, 8 messages in 12mo (clustered tight). T4 tips thread Apr 2, BATL Tips 2025 Mar 9, Employment Agreement Mar 9. Employer. Add as `employer` contact.

**Gerald Karaguni** — `gerald.karaguni@gmail.com`, 9 messages in 12mo. Neighbor. Fence Project thread Feb 11 → Apr 11. Referenced Alex's dog Tyson passing Feb 11 2026 (an important personal event — should be a journal/decision entry, not just contact metadata). Add as `neighbor`.

**Jon McLaren** — `jonmclaren@me.com`, ~3 direct messages + **active site project**. Vercel deployment-failure notifications for `jon-mclaren-vo` land in Alex's inbox → Alex is building his site. Confirmed by workspace dir `/wkspaces/jon-mclaren-vo`. Also in ACTRA Game Expo group thread. **Client + ACTRA peer.** Add as `client` + `peer`.

**Craig Burnatowski** — `craigburnatowski@gmail.com`, 2 direct messages Mar 27 (Game expo Sunday thread) + **active site project**. Alex explicitly told Ivan "I've been working on some new sites for Elana and Craig". Confirmed by workspace dir `/wkspaces/craig-burnatowski-site`. ACTRA Toronto peer + voice actor + site client. Add as `client` + `peer`.

### Non-qualifying / demote

**Tish Hicks** (VO Dojo) — `tishhicks@thevodojo.com`, 201+ messages — **all promotional**. Webinar announcements, class offers, "Ask the Sensei" Q&A reminders. Volume ≠ correspondence. Same treatment as ACTRA Toronto: orientation/broadcast-only, low-weight.

**Meghan Hoople** (CAVA) — `meghanhoople@cavavoices.org`, 2 messages (both the same webinar announcement). Skip.

**Myles Dobson** — `contact@mylesdobson.com`, ~3 unique messages, all in the Game Expo group thread. Not a direct correspondent. Below threshold. Skip as individual contact; he's part of the ACTRA Game Expo volunteer group, which could live as an `event_group` entity instead.

**Samy Osman / Cory Doran / Anna Morreale** — all zero direct messages. Only visible via CC in the Game Expo group thread. Same story — group-membership only, not contact-worthy individually.

**Alison Little / Jason Thomas** — zero direct correspondence. They appear only in Elana's email signature ("Principal agent: Alison Little / Voice agent: Jason Thomas"). These are **attributes of Elana**, not contacts. Store as string fields or via `entity_edges` with type `represented_by` targeting Elana, not as first-class contact rows.

---

## Workspace Signal (cross-reference)

Alex's `/wkspaces/` directory confirms active dev projects for five contacts:

- `alex-somerville-vo` — Alex's own site
- `craig-burnatowski-site` → **Craig Burnatowski** (confirms client)
- `elana-dunkelman-vo` → **Elana Dunkelman** (confirms client)
- `ivan-sherry-site` → **Ivan Sherry** (confirms client)
- `jon-mclaren-vo` → **Jon McLaren** (confirms client)

This is independent confirmation of the email audit's client findings — the website-build relationship is a real, load-bearing fact that should be encoded as `entity_edges` of type `building_site_for` (or similar) in next_soy. It's the kind of cross-module fact that the loci layer was designed to surface and that the current flat schema can't express cleanly.

---

## Recommended Seed Inclusion List for next_soy

### Primary contacts (11)

Entities to seed as full `contacts` rows with active status, full role metadata, and expected outward edges:

1. Jessica Martin (Grow App — client)
2. Anna Lee (accountant)
3. James Andrews (VO demo producer) — *dedupe id 7 + id 9*
4. Elana Dunkelman (friend + site client + ACTRA peer)
5. Jackie Warden (primary talent agent)
6. **James Somerville** (father + advisor) — NEW
7. **Ivan Sherry** (VO coach + site client) — NEW
8. **BATL HR / Shauna** (employer) — NEW
9. **Gerald Karaguni** (neighbor) — NEW
10. **Jon McLaren** (site client + ACTRA peer) — NEW
11. **Craig Burnatowski** (site client + ACTRA peer) — NEW

### Low-weight / broadcast-only entities (2)

Keep in next_soy but flag them so the loci layer doesn't burn budget expanding into them:

- ACTRA Toronto (union broadcasts)
- Tish Hicks / The VO Dojo (promotional broadcasts)

### Drop or migrate to `prospects` table (8+)

The talent-agency records with zero correspondence. Recommend a new `prospects` table distinct from `contacts` to preserve research value without contaminating the loci graph.

### Relationship edges to add

Independent of contact seeding, these edges should be materialized in `entity_edges`:

- Alex → building_site_for → Elana, Ivan, Jon, Craig
- Elana → represented_by → Alison Little (principal), Jason Thomas (voice)
- James Somerville → cc_regular → Anna Lee, Cameron Somerville, Chris Graham
- Jackie Warden → books_for → Alex (outcomes/audition edges rather than contact-to-contact)
- Alex → works_at → BATL
- Gerald Karaguni → neighbor_of → Alex

---

## Open Questions for Schema / Seed Plan

1. **Prospects vs contacts split** — add a `prospects` table, or reuse `contacts` with a `status='prospect'` flag? The edge cases (Kerry Morrison at "Demac Media (former)") make a `status` enum with values `{active, prospect, inactive, broadcast_only}` more useful than a hard table split. This is small enough to absorb into the next_soy DDL.

2. **Dedupe James Andrews** — rows 7 and 9 are the same person. Backfill plan should MERGE on email.

3. **Elana's agents as attributes vs edges** — `entity_edges(elana → alison_little, type='represented_by', role='principal')` is cleaner than a string field. Alison Little would need to exist as a thin contact row (name + agency only) for the edge to land — acceptable, and cheap.

4. **Family / personal contacts not in SoY today** — Cameron Somerville (brother), Chris Graham, Ainslie Roberts appear repeatedly in James Somerville's cc list. They should probably be seeded too (likely all clear the 5-msg/12mo bar through forwarded threads alone). Haven't queried them directly; flagging for a follow-up round before seed_next_soy.py runs.

5. **Event groups (Game Expo volunteers)** — eight ACTRA members in one thread but none qualify individually. Should there be an `event_groups` or `cohorts` table? This is the "shared context" pattern that loci graphs want to walk. Not a blocker — can defer to schema v2.

---

## Residual uncertainty

- Katherine Ryan (Buchwald) — email shared with Buchwald record; zero-count reflects both.
- The 201+ cap on `resultSizeEstimate` means for very-high-volume correspondents (Jackie, James Somerville, Ivan, Kerry, Cameron, Ainslie, ACTRA, Tish) I only have a lower bound. That's fine for threshold-clearing decisions.

---

## Round 2 — Follow-up queries (2026-04-11)

After the initial audit I ran four more queries to close residual uncertainty. Three of the four changed the picture meaningfully.

### Cameron Somerville (brother) — `cameron.somerville@gmail.com`

**201+ messages in 12mo.** Active in family threads, not just a passive cc target. Examples: "No no no, thet talking cats though..." (Mar 20 deepfake thread), "Beautiful" art exchange threads, cc'd on **"Time for a Will"** estate-planning thread Mar 28. Clears threshold easily.

**✓ ADD** as `family`. Edge: `family_of` ↔ Alex, `cc_regular_of` ↔ James Somerville.

### Chris Graham — `CGraham@constellationhb.com`

**13 messages + business relevance.** President of Constellation HB. The surprise: he's not just a dad-circle contact — there's a **"Re: Reprise" Mar 16–17 2026 thread** where Alex forwarded the Reprise tech stack doc, and James wrote "I chatted with Alex this morning as he described what he was working on, and I must admit that I wish we had a tool like this when building both Navtel, Microtest and Atelco, very impressive!" **Chris is an early external Reprise business touchpoint.**

Also appears in AI/business threads ("Industrial AI", "AI in the news, the phases", "Build Canada Homes proposal portal"). Two email addresses: `CGraham@constellationhb.com` (work) and `me@chrisg.ca` (personal) — needs to be modeled as one contact with multiple `contact_identities` rows.

**✓ ADD** as `advisor` + Reprise business contact. Edge: `prospect_for` → Reprise project.

### Ainslie Roberts — `ainslieace1@aol.com`

**201+ messages in 12mo.** Family (close enough to be cc'd on "Time for a Will" and "Will ideas Rev 2" — both Jan 28–29 and Mar 28 iterations of James's estate planning). Wellness-forward (supplement/microbiome/dopamine article forwards explicitly targeted at her). Surname "Roberts" overlaps with Peggy Roberts and Ian Roberts and Meredith Roberts in the same family-circle cc lists — this is a significant connected family cluster.

**✓ ADD** as `family`. Note the Roberts family cluster as a future memory_episode or edge subgraph.

### Kerry Morrison — `kmo@betterstory.co` — **UNDER-CALLED INITIALLY**

**201+ messages in 12mo.** I need to flag this clearly: the initial audit deferred Kerry because SoY has him tagged `"Demac Media (former)"` with no email, and I assumed he was a legacy/prospect record. **He is not.** Kerry is the single most load-bearing peer contact in the inbox after Jackie Warden, and almost all of it is about SoY itself.

Threads (sampling):
- **"Re: google multi-auth for SoY"** — Kerry is *implementing SoY features* and discussing architecture with Alex
- **"Alex shared 'SoY Showcase for Kerry' with you"** — extended thread about SoY showcase functionality, Kerry delivering feedback, discussing Telegram integration, remote Claude control, "diabolical. Quality marketing hack I didn't know I needed"
- **"Spec-Site Platform — where I'm at"** — direct collaboration on the Spec-Site platform (which lives in `/wkspaces/specsite`). Banter: "You got it, you lambasted twat stick" / "Please don't ever send me emails where you address me by my name. We've known each other too long."
- **Google Meet "Alex and Kerry" Mar 30** — scheduled 1:1
- Kerry at `kmo@betterstory.co` (currently at Better Story); also uses `kerry@softwareof.you` — wait, **that's a softwareof.you address**. Kerry apparently has an account on Alex's domain. Worth a manual check but suggests he's an early SoY user with his own data.

**✓✓ PROMOTE** from ghost to primary. Concrete changes:
- Update existing record (id 6): add email `kmo@betterstory.co`, update role from "Demac Media (former)" → `SoY collaborator / dev peer / close friend`. Company → `Better Story`.
- Add `contact_identities` row for `kmo@betterstory.co` and for `kerry@softwareof.you` if it's real.
- Add edges: `collaborator_on` → SoY (existing project), `collaborator_on` → Spec-Site platform, `close_friend_of` ↔ Alex.

### Bonus signals captured

Two facts surfaced that deserve first-class representation in next_soy even though they're not contact-level:

1. **"Time for a Will" thread (Mar 28 2026)** — James Somerville is actively planning his estate. Alex replied "I have no intention of exiting Dico" — there's an entity "Dico" I don't have context on (a family business? an investment vehicle?). This is a **major family memory_episode candidate**: "James's estate planning 2026", spanning Jan 28 ("Will ideas to be discussed"), Jan 29 ("Will ideas Rev 2"), Mar 28 ("Time for a Will"). Cc targets reveal the inner family circle: Cameron, Ainslie, Anna Lee, Alex.

2. **Reprise has external discussion already.** The Mar 16–17 thread forwarding the tech stack doc to Chris Graham means Reprise is already being socialized to potential business contacts — not just a solo project. This should be captured in the Reprise project's state.

### Revised qualifying-contact count

- **Round 1:** 5 existing-SoY qualifiers + 6 new-candidate qualifiers = 11 primaries
- **Round 2 adds:** Cameron Somerville, Chris Graham, Ainslie Roberts, **Kerry Morrison (promotion)** = 4 more
- **Revised total: 15 primary contacts** for next_soy seed

### Open item: "Dico" — RESOLVED 2026-04-11

Dico is **James Somerville's holding company**. Alex and Cameron Somerville are both shareholders. Seeds as a company-type contact (parallel to BATL Axe Throwing) with edges:
- `owner_of`: James Somerville → Dico
- `shareholder_of`: Alex → Dico
- `shareholder_of`: Cameron Somerville → Dico

Alex's "no intention of exiting Dico" reply in the Mar 28 will thread resolves: he's declining to sell his shares back. This makes the estate-planning episode queryable end-to-end once seeded.

### Kerry's `kerry@softwareof.you` address — CONFIRMED 2026-04-11

Confirmed as one of Kerry's addresses. Both `kmo@betterstory.co` and `kerry@softwareof.you` get `contact_identities` rows against the promoted canonical record (id 6). Side-effect: Kerry is an early external SoY user, not just a code collaborator — worth remembering but nothing for v1 schema.
