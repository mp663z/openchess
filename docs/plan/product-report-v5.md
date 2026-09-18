---
title: "OPENCHESS 2027 - v5"
---

<div class="cover">
<p class="cover-kicker">OPENCHESS 2027 &middot; v5 &middot; 18 September 2026 &middot; working name, final name pending</p>
<h1 class="cover-title">Never let your chess knowledge go stale.</h1>
<p class="cover-sub">ChessBase stores chess work. OpenChess maintains it - and now trains it.</p>
<p class="cover-lede">v5 makes two moves. The architecture goes asymmetric: a local-first desktop core does every byte of heavy compute - local models, Stockfish, the indexer, the delta engine - and a thin web/mobile habit surface owns the loop: the review queue, the approval gate, the quiet-week screen and training, joined by end-to-end encrypted sync. And the forum research folds in: two entry paths on one position-native memory layer - build-and-train from your own games, or maintain-and-triage an existing repertoire - with a diagnosis-to-plan loop for the players who were never going to import anything. The business goal is committed: 1,000 paying users at &euro;8/$10 during 2027.</p>
<table class="cover-meta">
<tr><td>Document</td><td>Product thesis + MVP spec + supporting report</td></tr>
<tr><td>Status</td><td>v5 - asymmetric architecture, committed business goal, and forum research folded in (18 Sep 2026); v4's kill-order structure, bets, spikes, fork, trust ramp and appendices kept intact</td></tr>
<tr><td>Evidence base</td><td>v2 base: 37 search batches, 798 discovered URLs across 162 domains, 40 sources deep-read. v5 adds the 18 Sep 2026 forum research report: 20+ pages across Chess.com and Lichess forums, Reddit/r/chess, expert interviews and official Lichess dataset pages.</td></tr>
</table>
</div>

<div class="pagebreak"></div>

# v5, on one page

## Ranked by what kills the company. v4's skeleton, with three changes on top: an asymmetric architecture, a committed business goal, and the forum evidence folded into the plan.

**v4 was organised by kill order, and v5 keeps that skeleton untouched - the three bets, the three killers, the spikes, the fork, the trust ramp, the appendices. Three things change on top of it.** First, the architecture stops being a desktop app that exports its habit to someone else's phone. It is now an asymmetric split: a **local-first desktop core** does every byte of heavy compute - local models, Stockfish, the indexer, the delta engine - private by default and free for the business to serve, while a **thin web/mobile habit surface** owns the loop: the review queue, the approval gate, the quiet-week screen and training, joined to the core by end-to-end encrypted sync. Training is owned; Anki and Chessable export remains a feature, not the phone story. The ambition is stated flatly: **the only platform a serious amateur improves on.**

Second, the forum research (18 Sep 2026) folds into the plan: **two entry paths on one position-native memory layer** - **build and train from my games** for the 1800&ndash;2300 club player, with repertoire construction first-class because most targets have no importable repertoire, and **maintain and triage my repertoire** for 2400+ - plus a **diagnosis-to-plan loop**: PGN in, recurring-error diagnosis, a three-item weekly plan, drills from the player's own positions, and a transfer check that measures whether the error stopped recurring. For casual players the front door is automatic: connect Lichess/Chess.com once and the games flow in on their own - the product answers with targeted, science-backed sessions sized in minutes, not hours of pro-style prep (&sect;9).

Third, the business goal is committed: **1,000 paying users at &euro;8/$10 per month during 2027 (~&euro;96k ARR)**, the funnel derived backwards from that number, and maximum user benefit per euro from Lichess CC0 data plus generative AI as first-class levers - with &euro;8 held inside the honest ChessTempo band (&sect;10).

| Kill order | The risk | Where v5 resolves it |
|---|---|---|
| 1 | The chosen segment (1800&ndash;2300) may not have the hero pain; theory staleness is acute at 2400+, weak below - and the forum research says the segment's real pain is the loop that never closes: games that never become a plan | Spike C answers H0 this week with an artifact classification (&sect;4); the fork is now two entry paths on one memory layer, decided with evidence (&sect;5) |
| 2 | The core IP - the delta scoring function - was undefined, with no naive baseline to beat | Fully specified (&sect;7, Appendix D); backtested offline before product code (Spike B, &sect;4, Appendix B) |
| 3 | The moat contradicted the openness promise: fully portable memory cannot be the moat | Defensibility restated honestly: distribution, habit, delta-model quality (&sect;3, killer 3) - v5 adds measured transfer and the zero-knowledge trust record to the same list |

**What v5 commits to, flatly:** the three bets (&sect;2), with Bet 1 restated to cover both entry paths. **What v5 changes in the architecture:** the export-first phone story is replaced by the asymmetric split; training moves in-house on the web/mobile surface; the approval gate moves to the phone; sync is zero-knowledge (&sect;9, &sect;12, &sect;14). **What v5 changes in the plan:** H0 now classifies the repertoire artifact (maintained, scattered, course-bound, head-only, absent); the fork branches become the two entry paths; the concierge loop becomes the diagnosis-to-plan loop; a parallel 2400+ probe joins the beta (&sect;4&ndash;6). **What v5 changes in the business:** a committed goal and a backwards-derived funnel; a genuinely great free tier as the marketing, the paid &euro;8/$10 buying hosted sync, bigger models, credits and coach features; and a coach/community GTM inside the &euro;8 price and the ~90% margin guardrail (&sect;10, Appendix C). **What v5 keeps intact, on purpose:** the kill-order structure, the spikes, the fork mechanics, the trust ramp, the pricing instruments, the rights matrix, the failure-mode table and every appendix - v4's skeleton carried this far and still holds.

**Reading order:** &sect;2 bets, &sect;3 killers, &sect;4 week one, &sect;5 the decision (shaped by the forum evidence), &sect;6 the twelve weeks, &sect;7&ndash;13 the machinery, appendices the protocols, arithmetic and change log.

<div class="pagebreak"></div>

# 2. The three bets

## What this company believes, stated flatly. Everything else in this document is a hypothesis behind one of these.

v2 stated targets as facts; v3 converted nearly everything into a hypothesis. Both fail as strategy. A strategy commits somewhere. These are the three claims OpenChess bets the company on. Each carries its kill condition, because a bet you cannot lose is a slogan.

<div class="bet">
<p class="bet-head">Bet 1 - The pain is real, weekly, and unserved - on both entry paths.</p>
<p>Competitive amateurs lose real time every week to one of two loops no incumbent closes. Players who maintain a repertoire lose it to <b>reconciliation</b> - detecting that something relevant happened, finding their prior thinking on the same position, deciding what changed, and turning the change into training. Players still building - most of the 1800&ndash;2300 segment, per the forum research - lose it to a loop that never closes: games are played, engine review lists mistakes without a plan, the same decision fails again next week, and no system notices the recurrence. One position-native memory layer serves both loops. The pain is felt most weeks a serious player plays.</p>
<p class="bet-kill">Kill condition: Spike C and the Phase 0 screen-shares show the target population does not, in fact, do either kind of work weekly - they skip it and do not care that they skip it.</p>
</div>

<div class="bet">
<p class="bet-head">Bet 2 - A delta engine can beat the dumb baseline.</p>
<p>A scoring function over public games, the user's repertoire, engine drift and the user's own results can surface decision-relevant changes <b>materially better than "any new 2500+ game in an ECO code you play, by recency."</b> If the naive rule does the job, the honest product is a ranked digest - useful, but not a company.</p>
<p class="bet-kill">Kill condition: the Spike B backtest fails to beat the naive baseline by the stated margin (&ge;15 percentage points of decision-relevance) at the 1800&ndash;2200 rating bands. No margin, no wedge; the twelve-week build does not start.</p>
</div>

<div class="bet">
<p class="bet-head">Bet 3 - Trust through evidence is the surface serious players adopt.</p>
<p>An approval-gated loop - visible diffs, cited evidence, no silent writes, graduated autonomy earned per decision class - is not a tax on the product. It is the reason the players this product needs most will let it touch their preparation. The approval gate and the ten-minute promise are reconciled by the trust ramp (&sect;9), not by choosing one.</p>
<p class="bet-kill">Kill condition: in the beta, users systematically rubber-stamp or ignore the approval step - evidence that the decision surface, not the detection, is the part nobody wants.</p>
</div>

Everything else - segment breadth, pricing, habit strength, performance budgets - sits behind these three, each with a test, a pass bar and a kill rule. If a bet dies, the document that follows is wrong and gets rewritten; that is what the bets are for.

<div class="pagebreak"></div>

# 3. The three things that could kill this, in order

## Each gets a resolution path with a date, not a section of equal length.

### Killer 1 - The segment without the pain

The hero problem is "theory moved and your repertoire went stale." That problem is acute at 2400+, where a new GM game in a critical line genuinely changes what a prepared player must play. At 1800&ndash;2300 the binding constraint is more often recall, time and blunders: a 2100 was rarely playing the critical line accurately enough for its refinement to matter. v3 noticed this tension and walked away from it - building for the segment whose need is least demonstrated while deferring the segment with the sharpest pain behind evidence gates.

**Resolution, not rhetoric.** Two cheap instruments answer this before product code: Spike C (twenty conversations answering H0 - does the target user even have a machine-readable repertoire?) and the Spike B backtest split by rating band (does the delta change decisions at 1800&ndash;2000, 2000&ndash;2200, or only at 2200+?). Their outputs feed the fork decision in &sect;5, which re-picks segment, onboarding, pricing and moat together. The 1800&ndash;2300 build target is now a hypothesis with a test, not a premise. The forum research (18 Sep 2026) is the first directional read, and it cuts both ways: at 1800&ndash;2300 the recurring pain is not theory freshness but the unclosed loop - games that never become a plan, errors that recur unrecognized - and most targets have no importable repertoire at all, while the acute staleness pain at 2400+ is real but smaller and unusually trust-sensitive. That shifts the prior toward the build-and-train path (&sect;5); the spikes still decide, because forum evidence is self-selected and directional, never load-bearing prevalence.

### Killer 2 - The core IP was a blank

v3 gave the confidence-decay inputs (14 new games, 3 GM adopters, engine drift +0.2, your score 1/4, last reviewed 7 months) and never said how they combine, where the threshold sits, or how the score personalises. That function is the product; everything else - import, storage, UCI integration, training export, the desktop shell - is commodity engineering that several open projects already do. v4 defines it: &sect;7 specifies inputs, combination, thresholds and personalisation; Appendix D gives the working specification; Spike B measures it against the naive baseline on public data before any product code exists.

### Killer 3 - The moat contradicted the openness promise

v3 claimed simultaneously that exit is free (one-command complete export, independent format readers) and that accumulated personal memory is the moat. Both cannot be load-bearing: if memory is fully portable by design, a competitor can fork the client, read the data, and the memory is not the moat. "Git for chess knowledge" was doing strategic work a slogan cannot support.

**The honest statement of defensibility, which v4 adopts everywhere:**

| Layer | Is it defensible? | Why |
|---|---|---|
| Personal chess memory | No - by design. It is the user's data, fully exportable, readable by independent tools | That is a trust feature and an adoption argument, not a moat |
| Distribution and the weekly habit | Yes | Habits migrate slowly; being the Monday-morning default is worth more than any schema |
| Delta-model quality | Yes, if Bet 2 holds | A scoring function that demonstrably beats the naive baseline on decision-relevance compounds with every accepted/rejected delta users feed it |
| Operations, trust record, trademark | Yes | The hosted service's reliability, the public record of never shipping a silent write, and the name are not forkable |

The export stays - it is the #1 adoption objection remover and the right thing - but v4 stops calling it a moat. The maintenance service is the business; its quality is the moat; openness is the marketing.
<div class="pagebreak"></div>

# 4. Week one: three spikes, no product code

## Cheap answers to fatal questions, before the twelve-week clock is allowed to start.

v3 committed to positioning, north star, seven capabilities, five hypotheses and a full architecture, then said a study "could completely change the product." Both cannot be true. v4 resolves it: nothing is pre-committed that the spikes can change. The twelve-week experiment in &sect;6 starts only after the fork decision in &sect;5, which starts only after these three spikes report.

| Spike | Question | Method | Cost | Go / no-go output |
|---|---|---|---|---|
| **S1 - CBH/Mega import** | Is reading the user's licensed ChessBase archive technically and legally viable? | Technical: build a throwaway extractor against real CBH/Mega files from a licensed copy; measure game/annotation fidelity. Legal: counsel reviews the EULA against "local indexing of the user's licensed copy only" (&sect;12) | Days, one engineer + one counsel session | **Go:** CBH import becomes a beta connector after V0.1, and the GTM headline survives. **No-go:** the migration story collapses to "import your PGN folder" - materially weaker, and known in week 1 instead of month 4 |
| **S2 - Delta backtest** | Does the scoring function (&sect;7) beat the naive baseline on decision-relevant changes, split by rating band? | Offline replay on public data: 50 real amateur repertoires, 2024&ndash;2025 elite games replayed chronologically; full protocol in Appendix B | ~2&ndash;3 weeks, no product | Model beats baseline by &ge;15pp at 1800&ndash;2200, or Bet 2 dies and the wedge is re-scoped or killed before the build |
| **S3 - H0 conversations** | What fraction of the target segment has a machine-readable repertoire at all - and what shape is it in? | Twenty artifact-led conversations with 2000&ndash;2200 players who play weekly, recruited from clubs and online communities; "show me your repertoire" - then classify the artifact: maintained, scattered, course-bound, head-only, or absent (the forum research classification) | One week, calendar time only | H0 pass (&ge;40% importable) keeps maintenance-first onboarding; fail pushes the fork toward build-and-train - the branch the forum research already favors (&sect;5) |

The order is deliberate. S1 protects the GTM headline, which rests on the riskiest row in the rights matrix. S2 protects the wedge, which rests on unproven intelligence. S3 protects onboarding, which assumes an object - a structured repertoire - that much of the segment may not have. Total cost: roughly three weeks of elapsed time and no product code. Total value: the company finds out what it is before it builds the wrong thing well.

<div class="pagebreak"></div>

# 5. The fork: two entry paths, one memory layer

## The decision every previous version avoided, made with evidence, before the build - and now shaped by the forum research.

H1 assumed users import an existing archive. The GTM headline assumed a ChessBase archive. The first-value story assumed a position you analysed three years ago. All three assume the user arrives with years of structured, importable analysis. The forum research says how wrong that assumption is for most of the segment: a large fraction of 1800&ndash;2300 players have a scattered PGN folder, a half-finished Chessable course, some Lichess Studies, and a repertoire that lives in their head. For them the job is **construction**, not maintenance - and the same research names the loop they actually need closed: games &rarr; diagnosis &rarr; plan &rarr; drills &rarr; transfer.

This is the fork, and it now has names: **two entry paths on one position-native memory layer.**

| | **Build and train from my games** (construction-first) | **Maintain and triage my repertoire** (maintenance-first) |
|---|---|---|
| Segment | The broader 1800&ndash;2300 weekly club player - the forum research's primary segment | 2400+, and the organised, import-ready minority below it |
| The pain | Games never become a plan; errors recur unrecognized; the repertoire is scattered, course-bound or head-only | Theory moved and the repertoire went stale; reconciliation is manual; information overload |
| Onboarding | PGN drop of recent games &rarr; recurring-error diagnosis &rarr; a draft repertoire derived from the user's own games plus chosen lines, with coverage gaps and conflicts exposed &rarr; the first three-item weekly plan | Import, index, first value the same evening |
| The weekly loop | Diagnosis-to-plan: import &rarr; diagnosis &rarr; three-item plan &rarr; drills from the user's own positions &rarr; transfer check in later games | The delta: "your repertoire went stale" &rarr; review with diffs &rarr; train accepted changes |
| The delta story | Decay starts once construction lands; the weekly queue works from week one on diagnosis, and deltas join as the repertoire becomes machine-readable | "Your repertoire went stale" - decay has an object from day one |
| Pricing | Closer to coaching/course tooling; different anchors, possibly higher willingness, different competitors | Service over existing assets; the &euro;5&ndash;8 band plausible |
| Moat | Delta-model quality + habit (Bet 2), plus measured transfer - proof the plan works, which no course marketplace can show | Delta-model quality + habit (Bet 2) |
| First twelve weeks | Concierge diagnosis-to-plan loop on the user's own games | Concierge maintenance loop on imported repertoires |

Underneath both sits the same machine: one position-native memory (&sect;7), the same import and index, the same scoring function, the same review surface. The fork decides which loop is the wedge and which is the second feature - not which product to build.

**The decision rule, committed in advance:**

1. If S3 shows &ge;40% of the target segment has an importable structured repertoire **and** S2 beats baseline at 1800&ndash;2200: **maintenance-first** at 1800&ndash;2300, with the build-and-train path following as the second entry.
2. If S3 fails but S2 passes at 1800&ndash;2200: **build-and-train first** - construction-first onboarding into the same memory layer and delta backend. The forum research predicts this branch; the product pivots its first hour, not its architecture.
3. If S2 passes only at 2200+: **re-segment** - the wedge becomes maintain-and-triage at 2400+ (the forum research's pro mode: transposition-aware triage, opponent briefs), and pricing/moat get rewritten (this invalidates large parts of &sect;10; the document is rewritten, not patched).
4. If S2 fails everywhere: the delta is a ranked digest and Bet 2 is dead. The build-and-train path survives only if its transfer metric holds without the delta. The honest options are a small digest product, a training-plan product without a moat, or stop. The twelve weeks do not start.

The decision is taken at the end of week 3 with the spike results on one page. There is no default: no evidence, no build. The forum research moves the prior toward construction; it does not cast a vote.

<div class="pagebreak"></div>

# 6. The twelve weeks, re-sequenced

## The experiment runs after the fork, against the corrected hypotheses.

**Weeks 1&ndash;4 - concierge loop (thinnest possible).** Import the user's PGN drops (no account sync) and run the fork-selected loop by hand. Default branch (build-and-train): recurring-error diagnosis from the user's own games - decision errors, not just centipawn loss - a three-item weekly plan ranked by recurrence, game impact, confidence and trainability, drills built from the user's own positions with sources attached, review with visible diffs. Maintenance branch: the scored delta behind a hand-held backend, review, train. Either way it must run one real user's real week before anyone else touches it.

**Weeks 5&ndash;12 - thirty-user beta.** Recruited from the fork-selected segment (default: 2000&ndash;2200 players who play weekly - the primary study population). H3 runs as an A/B against the naive baseline: each user's deltas are ranked by both the model and the baseline in parallel, and decision-change rates are compared per user. **A parallel 2400+ probe** - five titled or near-titled players - tests whether the transposition-aware delta inbox saves expert time and changes enough decisions to justify the maintain-and-triage mode; it is directional, not a gate, and access difficulty is itself a finding (&sect;11). Beta recruitment runs through coach partnerships first: coaches' rosters *are* the 2000&ndash;2200 weekly segment (&sect;10, coach and community GTM). Casual-cohort onboarding uses the one-connect sync - activation for a casual user is connect account &rarr; auto-import &rarr; first diagnosis &rarr; first optimal session (&sect;9).

**The corrected hypothesis set.** H5 is demoted from gate to signal; the landing-page test becomes the primary willingness-to-pay instrument (&sect;10). Two hypotheses from v3 are re-scoped because their designs were confounded.

| # | Hypothesis | Pass | Fail |
|---|---|---|---|
| H0 | The target user has a repertoire the system can operate on - and v5 needs to know its shape | &ge;40% of 20 conversations show an importable structured repertoire; the artifact classification (maintained / scattered / course-bound / head-only / absent) is reported either way and designs the onboarding | &lt;40% &rarr; fork to build-and-train |
| H1 | Onboarding friction is solvable - PGN drop on the pro path, one-connect account sync on the casual path | &ge;70% of beta users reach first value in &lt;15 min | Median &gt;30 min or &lt;50% complete |
| H2 | The Inbox is a weekly habit | &ge;40% open it in 3 of 4 consecutive weeks **on the automated product**; the concierge phase is directional only and cannot pass or fail this hypothesis (&sect;8) | &lt;25% weekly retention on the automated product |
| H3 | The delta changes decisions, durably | &ge;40% of surfaced deltas produce an accepted repertoire change **still in the repertoire at 30 days**, and the model arm beats the naive-baseline arm by &ge;15pp | Model arm fails to beat baseline, or accepted changes revert within 30 days |
| H4 | The loop is fast enough as a weekly habit | Median weekly human time &le;25 min at the observed N (decisions/week), with N measured inside the funnel (&sect;9) | Median weekly human time &gt;45 min, or N collapses to near zero for most users |
| H5 | (Demoted - signal, not gate) Beta users prepay | Reported alongside the landing-page test | n/a - see &sect;10 |

**New measurements, gated from day one** (the missing metrics the critique named):

| Metric | Why it is gated now |
|---|---|
| 8-week retention (past the 4-week novelty window) | A habit product that cannot hold week 8 has no business model |
| Cost to serve per user per month (sync storage + bandwidth + control plane + generative credits; compute stays on the user's desktop, &sect;14) | Determines whether &euro;8/$10 is viable at all |
| Churn, with exit reason captured | Guilt-driven churn is a named failure mode (&sect;13) |
| Over-the-board proxy: recurring-error rate over 90 days - the diagnosis-to-plan loop's transfer check | The right instinct from v3's scorecard, now actually gated |
| Quiet-week rate per user | Calibration check for the precision policy: an honest amateur should see mostly quiet weeks (&sect;9) |

**Decision rule, unchanged in spirit, corrected in content:** H2 (on the automated product) and the landing-page WTP test are the product. If both fail, the wedge is wrong and the architecture does not matter yet.
<div class="pagebreak"></div>

# 7. The core IP: the delta scoring function

## The one thing in this document that is not commodity engineering - now specified.

Confidence decay was a blank in v3: inputs listed, combination never defined. Here is the combination. The design principles come first, because they constrain everything after:

1. **Transparent, not learned - at first.** A published weighted score with inspectable components. The trust architecture (&sect;12) demands the user can ask "why did this surface?" and get an arithmetic answer. A learned ranker is a later, evidence-gated upgrade.
2. **Personalised by construction.** The same new game must score differently for a 1700 London System player and a 2400 Najdorf player. Rating band and line frequency are first-class inputs, not post-hoc filters.
3. **Measured against the dumb rule, always.** Every report of model performance includes the naive baseline's performance on the same data.

### The score

For each (repertoire line, evidence window) pair, the delta score is:

<div class="formula">
<p><b>S(line) = w<sub>T</sub>&middot;T + w<sub>E</sub>&middot;E + w<sub>D</sub>&middot;D + w<sub>R</sub>&middot;R + w<sub>A</sub>&middot;A</b> &nbsp;&nbsp;multiplied by&nbsp;&nbsp; <b>F &middot; G</b></p>
</div>

| Component | What it measures | Example signal |
|---|---|---|
| T - Theory activity | Volume of new master games in the line, weighted by game quality | 14 new games this month, 3 above 2600 |
| E - Elite adoption shift | Direction of elite behaviour change, not just volume | 3 GMs switched from 12&hellip;Nf6 to 12&hellip;Re8 |
| D - Engine eval drift | Movement of verified engine evaluation at the user's tabiya | +0.2 toward White since last review |
| R - Own results | The user's recent score in the line, minimum-sample guarded | 1/4 in the last 60 days |
| A - Age since review | Time since the user last reviewed the line | 7 months |

| Modulator | What it does | Why |
|---|---|---|
| F - Line frequency | Multiplies by how often the position actually occurs in the user's own games | A delta in your main weapon matters; a delta in a line you reach twice a year does not |
| G - Rating-band attenuation | Down-weights E and T for lower rating bands; up-weights R and D | The critique's core point: at 2100, elite fashion rarely changes what you should play, but your own results and eval drift always do |

Weights (w) and the modulator curves (F, G) are **initial guesses, set and frozen by the Spike B backtest, then re-fit monthly against accept/reject feedback**. Appendix D carries the working specification with starting values and the freeze discipline. The score is per-line; the Inbox shows the top few lines above a per-user threshold.

### The threshold is a precision policy, not a constant

Surfacing threshold is tuned per user to a target precision, defaulting conservative: the product would rather show two items that matter than six of which two matter. Accept/reject feedback adjusts the threshold weekly. This is the direct answer to false-positive costing: an inbox that teaches the user to stop opening it is the named death of this product (&sect;9, &sect;13), so the system is built to say less.

### The naive baseline, stated flatly

> **Baseline: any new game rated 2500+ in an ECO code present in the user's repertoire, sorted by recency.**

If that rule achieves 55&ndash;60% perceived relevance, a 70% relevance target is a ranked list, not intelligence, and there is no moat. Bet 2 exists precisely because this might be true. Spike B measures both on identical data before product code; H3 keeps the comparison running inside the beta as a permanent control arm. The day the model stops beating the baseline is the day the company knows its moat has evaporated, from its own dashboard rather than from a competitor.

### Position equivalence &ne; analytical equivalence - kept, still load-bearing

The scoring function inherits v3's most subtle technical point, because relevance depends on it. Two records refer to the "same" position only when variant, side to move, castling rights and en-passant state all match - never identified by a single collision-prone hash. But even a truly identical position can carry different analytical weight: reached via a different move order, it may dodge the user's repertoire or land in a different opening classification. The model therefore separates **position identity** (canonical state - what dedupes analysis and memory), **move-order path** (how the position was reached - preserved, never collapsed) and **repertoire context** (which of the user's lines it belongs to - what T, E, D and F above run against). Transpositions converge for *memory* (your old Najdorf analysis is found) while staying distinct for *relevance* (the delta is scored against the line you actually play). Almost everyone gets this wrong; getting it right is what makes the score meaningful.

### What this makes possible that v3 could not say

The decay state machine (a line is reviewed &rarr; evidence accumulates &rarr; score crosses threshold &rarr; surface &rarr; decision &rarr; trained &rarr; reviewed) is now executable: every arrow has a number attached. The ten-minute funnel (&sect;9) can budget N because the threshold controls N. And the quiet week (&sect;9) is well-defined: S below threshold everywhere, reported honestly. The same position-native layer feeds the build-and-train path: recurring-error diagnosis is classification and retrieval over the user's own positions, with engines as verifier and small local models doing classification, ranking and planning (&sect;14) - never one opaque model inventing chess truth.

<div class="pagebreak"></div>

# 8. The measurement corrections

## Three designs that could not have measured what they claimed, corrected.

**H2 was confounded by the concierge.** "40% open it in 3 of 4 consecutive weeks," measured on thirty users receiving attentive hand-held human service, is evidence about the service, not the product. People open doors held by concierges. The correction: full blinding is impractical at this scale, so H2 is **restated** - the concierge phase produces directional signal only and is explicitly incapable of passing or failing H2; the hypothesis is gated on the automated V0.1 product, where opens are measured with no human in the loop. The concierge beta still returns H1, H3, funnel timings and every qualitative signal it can honestly carry.

**H3 measured the wrong thing.** "70% of surfaced deltas rated relevant" invites users to rate novelty as relevance. Relevance was never the question; the question is whether the delta changed what the user plays, and whether the change survived contact with a month of games. H3 is now behavioural: accepted repertoire change still present at 30 days, with the naive baseline running as a permanent control arm (&sect;6).

**The ten-minute funnel ignored N.** v3's decomposition was a genuine improvement - right instinct, wrong allocation. It budgeted machine latency carefully and gave human review "under 3 minutes," but the real weekly cost is 3 minutes multiplied by N decisions plus the training session, and N was nowhere in the model. Six items at 3 minutes is 18 minutes before training. N is now inside the funnel (&sect;9), the weekly budget is stated as a function of N, and the precision policy (&sect;7) is what keeps N honest - which conveniently aligns the product's quietest design goal with its loudest promise.

**And the missing metrics are in.** Retention past four weeks (8-week retention, gated), cost to serve per user, churn with exit reasons, and an over-the-board proxy (recurring-error rate over 90 days, gated rather than decorative) are in the hypothesis set and scorecard (&sect;6, &sect;15).
<div class="pagebreak"></div>

# 9. The product, revised

## Designed for its most common state: a quiet week. Reframed from debt to gain. Owned, on the phone.

### The ambition: the only platform to improve on

v4's phone story was export: approve on the desktop, recall in someone else's app. That made the habit a tenant in Chessable's house. v5 owns the habit. **The ambition is the only platform a serious amateur improves on** - memory, diagnosis, plan, drills and maintenance in one place, with the phone as the surface where improvement actually happens.

The shape is an asymmetric split:

* **The desktop core is local-first and does every byte of heavy compute.** Import, the position index, Stockfish verification, small local models for classification and ranking, the delta engine - all on the user's machine, private by default, offline-capable, crash-safe. Per-user compute cost to the business is zero, because the user owns the computer.
* **The web/mobile surface is thin and owns the habit.** The review queue with visible diffs, the approval gate, the quiet-week screen, the three-item weekly plan and training - drills built from the user's own positions, scheduled by observed forgetting and line frequency. It runs in a browser on the phone the user already carries; nothing heavy ships to it.
* **End-to-end encrypted sync joins them.** The desktop encrypts; the server stores opaque blobs and relays them; the web/mobile surface decrypts locally. The server never sees a game, a line, a note or a plan in plaintext, and the hosted control plane - billing, entitlements - is content-blind by construction (&sect;12).

Anki and Chessable export remains, as a feature for users who already train there - not as the training story. The fork's two entry paths (&sect;5) meet on this surface: build-and-train users see diagnosis, plan and drills; maintain-and-triage users see the delta queue; both read the same position-native memory.

**The two personas, stated flatly.** The **pro path**: PGN folders and CBH archives, hours of preparation, the maintenance loop - the player who treats Tuesday like a training camp. The **casual path**: connect the account once and the games flow in automatically (account sync, &sect;11) - then the product serves targeted, science-backed sessions sized for a lazy player to finish: spaced retrieval against the user's own missed decisions, a bounded number of decisions per week (N stays small by the &sect;7 precision policy), adaptive difficulty driven by observed transfer, sessions measured in minutes. The casual player never performs the export ritual and never gets homework; the science sits underneath, the session on top. Both personas meet in the same weekly loop and the same habit surface.


### The quiet week is the product's most common state

For an honest amateur, the majority of weeks should be quiet: theory did not move in a way that changes what they play. v3 had no design for this state, which means it had no design for most of its product's life. The failure fork is brutal: show an empty inbox and the weekly habit dies of nothing-to-do; pad the inbox and precision dies, then the habit dies of noise. Habit products die of noise, not latency.

The resolution is deliberate and two-sided:

* **Never pad.** The precision policy (&sect;7) is sacred; a surfaced item must earn its place. The moment the product inflates a quiet week, it has started lying, and the trust architecture forbids it.
* **Make "quiet" a valuable screen, not an empty one.** The quiet-week Inbox reports the work, not the absence: what was scanned this week (games in your lines, elite activity, your own new games), the current confidence state of your repertoire, and the gain framing below. It lives on the web/mobile surface, where the week is actually checked.

### The emotional contract: gain, not guilt

"The Inbox makes staleness visible" is a churn mechanism: a weekly debt notification produces unsubscribes, not habits. v4 reframes the same data around preparation and gain:

<div class="inbox">
<div class="inbox-title">OpenChess - Monday, 7:00 &middot; a quiet week</div>
<div class="inbox-row green">You are prepared for Saturday. <span class="sub">All 14 lines in your active repertoire are current</span></div>
<div class="inbox-row blue">Scanned this week: 2,140 new games, 31 in your lines - 0 change what you play <span class="sub">why &rarr;</span></div>
<div class="inbox-row green">You now know something you did not know last week: <span class="sub">12&hellip;Re8 is being tested at 2600+; your 12&hellip;Nf6 is holding</span></div>
<div class="inbox-row amber">4 decisions are due for recall - 10-minute session keeps them yours <span class="inbox-cta">train &rarr;</span></div>
<div class="inbox-foot">A busy week looks the same, with the changes first. Nothing changes until you approve it - here, on the phone - and after trust is earned, some things change with your standing approval (&sect;9, trust ramp).</div>
</div>

Same data, opposite contract: not "your knowledge is rotting," but "you know exactly where you stand, and here is what you know that you did not know last week." Staleness appears when real, as a specific, evidenced item - not as ambient debt.

### The graduated trust ramp

v3's approval gate was the best-argued part of the document and in permanent tension with the ten-minute promise: approve everything, and the product is a well-researched to-do list, not an agent. The ramp resolves it - autonomy is earned per decision class, never globally. The gate itself moves to the web/mobile habit surface: approval is a thirty-second action on the phone, not a desktop chore. Approvals apply locally, sync back to the desktop core end-to-end encrypted, and every write - approved or auto-applied - lands in the same log:

| Level | Behaviour | Entry condition | Exit condition |
|---|---|---|---|
| L0 - Manual | Every proposed change requires accept/reject with a visible diff | Default for every user and every class | - |
| L1 - Auto-apply, low-risk class | A defined class (e.g. re-annotation with verified engine updates; training-card refreshes) auto-applies; the weekly log shows every write with one-click revert | &ge;20 consecutive accepted decisions of that class, zero rejections | First rejection or revert &rarr; back to L0 for that class |
| L2 - Auto-apply, material class | Repertoire-move updates inside the user's existing lines auto-apply, flagged prominently in the log | L1 stable for 8+ weeks and &ge;50 accepted of the class | Any revert &rarr; L1; two reverts &rarr; L0 |
| Never automated | New-line additions, deletions, anything affecting shared/published artefacts | - | These always require a diff and an explicit decision |

The log is first-class UI: every automated write is listed, reversible, and reviewable in the same weekly session. The ten-minute promise and the approval promise stop fighting because the user reviews *a log of things that already happened correctly* instead of *a queue of things demanding attention* - and the trust architecture's core rule survives intact: no silent writes, ever. An auto-applied write with a prominent log entry is not silent; an unlogged one would be.

### The funnel, with N inside

The decomposed funnel survives from v3 - right instinct - with the allocation corrected. Machine stages keep their budgets; the human stage is the one the product manages:

| Stage | Budget hypothesis | Scales with N? |
|---|---|---|
| Ingestion (game appears &rarr; indexed; automatic on the casual path via account sync) | &lt; 30 sec | No - background |
| Relevance detection (scoring function) | &lt; 5 sec/batch | No - background |
| Position retrieval (memory + prior analysis) | p95 &lt; 250 ms | No |
| Engine verification | Declared budget, typical &lt; 2 min | No - queued |
| **Human review** | **&lt; 3 min per decision, on the phone** | **Yes, linearly - the cost the product must control** |
| Training session load (web/mobile drills) | &lt; 30 sec to today's queue | No |

The weekly human cost is now modelled explicitly: **T<sub>week</sub> = N &times; t<sub>review</sub> + t<sub>train</sub>**, with N (decisions surfaced per week) controlled by the precision policy and t<sub>review</sub> falling as trust levels rise. Training runs on the web/mobile surface; Anki/Chessable export remains available as a feature, off the critical path.

<div class="pagebreak"></div>

| N (decisions surfaced) | Review time at 3 min each | + 10-min training | Weekly total |
|---:|---:|---:|---:|
| 0 (quiet week) | 0 | 10 min | ~10 min |
| 2 | 6 min | 10 min | ~16 min |
| 5 | 15 min | 10 min | ~25 min |
| 8 (precision has failed) | 24 min | 10 min | ~34 min |

<img class="chart" src="assets/weekly-time-vs-n.png" alt="Weekly human time vs decisions surfaced" />

The claim is restated honestly: **a ten-minute quiet week, a sub-30-minute busy week, with N small by design.** "Under ten minutes" survives only per decision and on quiet weeks; v4 stops claiming it for the week. N is measured inside the funnel per user (H4), and a rising N is treated as a precision regression, not engagement growth.

<div class="pagebreak"></div>

# 10. Business reality, stated flatly

## The arithmetic, the company type, and the price ceiling - on the page, not implied.

### The market sizing, roughed out honestly

v3 never did the arithmetic. Here it is (detail in Appendix C):

* The paying pool is competitive amateurs roughly 1800&ndash;2300 who will pay for a platform that improves their game - plausibly **tens of thousands worldwide, not hundreds of thousands**.
* **The committed goal: 1,000 paying users at &euro;8/$10 per month during 2027 - ~&euro;96k ARR.** That is the bear row of the scenario table, chosen as the plan rather than feared as the downside: **1,000 paying &rarr; ~&euro;96k; 5,000 &rarr; ~&euro;480k; 10,000 (optimistic) &rarr; ~&euro;960k** at the committed price.
* The funnel, derived backwards from 1,000 paying: at a 20% free-to-paid conversion that is **5,000 active free users**; at a 5% visitor-to-free conversion that is **100,000 visitors** during 2027 - about 8,000 a month from community, content and word of mouth in the forums this report's evidence is drawn from, which is exactly the distribution the GTM already runs (&sect;11). Each rate is a hypothesis with an instrument: the landing-page test measures checkout starts, the beta measures free-to-paid, and the funnel is re-derived monthly from real numbers (Appendix C).
* Against that: a cross-platform desktop core with local models and engine integration, a position-indexed store, a scoring engine, a thin web/mobile habit surface, an E2E-encrypted sync relay, and a control plane with billing and fraud controls.

<img class="chart" src="assets/arr-scenarios.png" alt="ARR scenarios at EUR 5-8 per month" />

**Company type, committed:** this is a **small, open, durable business** - 1,000 paying users at &euro;8/$10 is a very good one if cost to serve stays near zero and retention holds - **whose product is #1 in quality for its segment**. It is **not, on current evidence, a venture-fundable thesis**, and this document is no longer written in the register of one. Two findings could reopen that question: the fork resolving toward construction/coaching with higher willingness to pay (&sect;5), or the coach/team evidence gates (&sect;11) passing strongly. Until then, the plan spends like a small business: spikes before builds, concierge before product, web surface before native platform.

### The price anchors were a warning, not support

v3 benchmarked Mega at &euro;229.90, ChessBase packages at &euro;349&ndash;499, Chessable at $11.99/month - all **content businesses** selling curated games and annotated courses. OpenChess sells no content; it sells a service over content the user already owns or licenses elsewhere. Selling maintenance at content prices, with no content, is the hard part. The honest comparator is productivity SaaS; the honest band for a hobby tool at this segment is **&euro;5&ndash;8/month - the ChessTempo band**. The committed price sits at the top of it: **&euro;8 / $10 per month**, defensible only if the product is visibly #1 in quality - which is why the benefit levers below get funded before any growth lever. The full benchmark table is kept in Appendix A - reframed as the warning it is.

### The benefit levers: CC0 data and generative AI, first-class

The goal is maximum user benefit per euro, and two levers are first-class:

* **Lichess CC0 data.** Bulk games, puzzles and engine evaluations - public, licensed for this use (&sect;12) - are the training and retrieval base for the small local models and the evidence store: rating-appropriate model positions, mistake prediction by rating band, drill material from the user's own positions. Open data is a wedge, not a moat; it costs the business nothing per user because it is indexed on the user's own desktop.
* **Generative AI, as the explanation layer.** Natural-language explanations of deltas, diagnosed errors and weekly plans; conversational review of the user's own games and lines - always grounded: the model explains evidence, it is never the chess evidence (&sect;12). Default: small local models on the desktop core - zero per-user compute, and the free tier's entire explanation layer. The paid tier adds hosted, heavier models under its monthly credit bundle for users who out-draft a laptop; BYOM keys remain supported; everything stays off the critical path.

Both levers compound the same moat list (&sect;3): calibrated personalization, measured transfer, habit. Neither replaces the engine as verifier or the scoring function as ranker.

### The free tier is the marketing; the &euro;8 is mostly infra and credits

v5's last business commit: **the free OSS tier is genuinely great, on purpose.** Everything the local-first architecture already makes free stays free - the full local loop: import (PGN drops, watched folders, the casual one-connect sync), position-indexed memory, the delta engine, recurring-error diagnosis, the three-item weekly plan, drills from your own positions, training and review, on the machine it is installed on. The desktop core carries its own compute, so a free user costs the business about nothing per month - generosity is cheap, and it is the entire top of funnel. The public position is flat: **the best free chess harness and coach, open source.** Every forum thread, coach recommendation and word-of-mouth mention points at a product that is free forever, not a trial that expires.

**What &euro;8/$10 actually buys:** hosted, zero-knowledge sync across devices (the free loop lives on one machine; paid puts the review queue and training in your pocket); hosted, bigger models for analysis and explanation heavier than a laptop runs well; a monthly bundle of generative credits for the explanation layer, metered so heavy use is covered instead of throttled; and coach features - the dashboard, roster views and shared weekly plans. The payment is mostly infrastructure plus bundled LLM credits, and the price stays honest precisely because the free tier keeps the comparison fair: nobody is asked to pay for what the local loop already does.

**The tension, named:** a genuinely great free tier caps conversion - some users the old funnel would have charged will now never need to pay. Accepted, not hidden: the 20% conversion assumption is re-read as free-to-paid and measured in the beta (Appendix C), and the paid pitch never degrades the free loop to force upgrades - that move would burn the OSS credibility the distribution runs on. Paid buys convenience (sync), scale (bigger models), generative usage (credits) and collaboration (coaches): the four things the local loop cannot provide at zero per-user cost.

### Willingness to pay: the instruments, re-weighted

1. **Landing-page price test (now primary):** tiers around the committed &euro;8/$10 price shown to segmented traffic; the metric is checkout starts, not clicks. Runs during the concierge beta, costs almost nothing, and measures strangers, not beneficiaries.
2. **Prepay inside the beta (demoted to secondary):** ten of thirty hand-held users prepaying &euro;60 is &euro;600 of evidence from a self-selected group receiving concierge service. Better than nothing; not a willingness-to-pay finding; reported alongside, never as the gate.
3. **Revealed-preference close (unchanged):** every study session ends with "what do you currently pay for chess, and what did you last cancel?"
4. **Cost to serve per user per month** is measured from the first beta week (sync storage, bandwidth, control plane and metered generative-credit consumption - the desktop core carries engine and model compute, so the free loop's per-user cost is zero) - because at &euro;8/$10, the margin is the business model.

### Commercial packaging (companion structure brief, 18 Sep 2026)

The OSS/hosted structure brief delivered alongside v3 is adopted: the complete chess product is **AGPL-3.0-or-later**; the hosted business lives in a **narrow proprietary control plane** (identity, billing, entitlements, LLM key routing, quota/fraud) in a separate repo behind documented APIs; contributors sign a **narrow non-exclusive CLA** for substantive changes (DCO for drive-bys); the company keeps release, trademark and roadmap authority. This supersedes v3's GPL-desktop/AGPL-server split and keeps the fork defense where it actually lives: trademark, official channels, operations and delta-model quality (&sect;3, killer 3). BSL was rejected: source-available is not open source, and the launch depends on OSS credibility.

### Coach and community GTM

The funnel needs ~100,000 visitors in 2027 with no store discovery (&sect;11). **The free tier is the first channel** - positioned as the best free chess harness and coach, open source, it turns community attention into active free users at zero marginal cost. Three community channels carry the rest, each sized to fit inside the committed &euro;8/$10 price and the ~90% gross-margin guardrail - about &euro;0.80 of variable cost per user-month, of which sync and the control plane use roughly &euro;0.15&ndash;0.30. Each channel's cap derives from the cost-to-serve measurement (&sect;6) and flexes with it:

* **Coaches as champions.** Chess coaches - most of them former competitive players - already do by hand what the product automates: spot a student's recurring errors, assign the week's work, check whether it stuck. The **coach dashboard** gives them that view across their roster: students' recurring-error themes, weekly plans and transfer checks - read-only, approved per student, revocable, zero-knowledge like everything else (&sect;12). Coaches earn a **revenue share on referred subscribers**, capped so the blended margin keeps the guardrail: ~5% of referred net revenue for the first 24 months (or 10% for 12, &asymp;&euro;0.40 per referred user-month), never negotiated past the guardrail. Coaches also solve beta recruitment: their rosters *are* the 2000&ndash;2200 weekly segment, so Spike C and the thirty-user beta recruit through coach partnerships first (&sect;6).
* **User referral.** One free month for the referrer when a referred friend completes three paid months - cheap, measurable, and native to a product whose users sit in clubs and online communities. The cost (&asymp;&euro;8 per successful referral) is acquisition spend, counted in the same guardrail arithmetic.
* **Paid titled-player experiences.** Simuls and play-a-titled-player sessions, sold to the same community through coach and club partners: revenue in their own right (titled players are paid per event), marketing for the platform, and the credibility a small open business cannot buy with ads. Monthly cadence; event economics are ticketed separately and never touch subscription margin.

<div class="pagebreak"></div>

# 11. Scope and sequencing

## Seven capabilities was still a year of work dressed as four months. V0.1 is five - and two of the five are deliberately boring.

### V0.1 scope, after the cut

1. **Import:** PGN files and folders, dropped or watched - **plus one-connect Lichess/Chess.com account sync on the casual path**, a partial return of the V0.2 cut. The casual player will not perform the export ritual, so on that path the connect happens once and games flow in automatically; the pro path keeps PGN-drop and CBH as primary. Sync buys OAuth maintenance, rate limits and two provider relationships, so it ships scoped to the casual onboarding fork - nothing broader.
2. **Search:** player, position, move sequence.
3. **Personal memory:** comments, engine analysis and source games retrieved by position, with full provenance, regardless of which file they came from.
4. **Repertoire + Delta + Diagnosis:** versioned lines with transposition-correct matching; the scoring function (&sect;7) computed on every import; recurring-error diagnosis over the user's own games, feeding the weekly plan.
5. **Review + Training, on the web/mobile habit surface:** approve/reject against cited evidence with visible diffs; the three-item weekly plan and drills built from the user's own positions; E2E encrypted sync to the desktop core. Anki/Chessable export ships alongside - a feature for users who already train there, not the training story.

**The phone is ours now.** v4 exported training to Anki and Chessable because desktop-only training was a real adoption risk. v5 closes that risk by owning the surface where the habit lives: the review queue, the approval gate, the quiet-week screen and training run on a thin web/mobile app, E2E-synced to the desktop core. A native mobile app stays deferred - the web surface covers the behaviour at a fraction of the cost. Anki and Chessable export ships from the first beta build as a feature; it is no longer the phone story.

**Still explicitly out:** team collaboration, remote workers, plugin marketplace, CQL, provider marketplace, the visual agent canvas, air-gapped packages, native mobile, account sync beyond the casual one-connect flow (rest of V0.2), CBH import (spike-gated beta connector, &sect;4). Each is real; none tests the wedge.

**Sequencing, final:**

| Stage | Window | Contents | Gate to proceed |
|---|---|---|---|
| Spikes | Weeks 1&ndash;3 | S1 CBH go/no-go, S2 delta backtest, S3 H0 conversations (&sect;4) | Fork decision (&sect;5) - no evidence, no build |
| Concierge MVP | Fork weeks 1&ndash;4 | Thinnest loop: PGN drop &rarr; diagnosis &rarr; three-item plan &rarr; drills (maintenance branch: PGN drop &rarr; delta &rarr; approve &rarr; train) | Runs one real user's real week |
| Concierge beta | Fork weeks 5&ndash;12 | 30 users, 2000&ndash;2200 weekly players, H0&ndash;H4 measured, model-vs-baseline control arm; parallel 2400+ probe (5 players) on the transposition-aware delta inbox | H2 on the automated product + landing-page WTP (&sect;10) |
| V0.1 productized | Months 4&ndash;8 | The five capabilities: cross-platform desktop core + thin web/mobile surface, offline, crash-safe, E2E encrypted sync, full export, p95 budgets (&sect;14) | Budgets pass on the &sect;14 benchmark; 100 users keep it 8 weeks |
| Depth | Evidence-gated | Account sync, dossiers, TWIC watchers, coach features, CBH connector if S1 passed | Validation gates below |

Even after the cut, months 4&ndash;8 is aggressive for a small team; the named further-cut order, if the schedule slips, is: search depth first, memory-UI polish second - never the delta, the review UX, or the habit surface.

### The validation study, re-aimed at the actual target user

v3's core study recruited ten titled players while the target user was 1800&ndash;2300 - observing a population the company had chosen not to serve. Corrected: the **primary study is 2000&ndash;2200 players who play weekly**, screen-sharing the same protocol ("show me exactly what you did the last time you prepared for an opponent"), run inside Spike C's twenty conversations. **Titled players become a secondary read** on the deferred elite segment - five sessions, where access difficulty is itself the finding. The elite/coach evidence gates are unchanged: no gates passed, no elite features.

### What do the incumbents ship in 18 months, if this works?

v3 treated incumbents as static. They are not. The row that was missing:

| Incumbent | Their 18-month fast-follow, if the wedge proves out | What actually blunts it |
|---|---|---|
| Chessable | Recall loops generated from the user's own research - they own the phone habit, the SRS loop and the content library; this is one feature away for them | Memory + provenance depth across the user's whole archive, open formats, desktop privacy - and, new in v5, our own training surface, so the recall loop is no longer theirs by default |
| Lichess | A staleness signal on the opening explorer as a free feature | Free and casual-scale; no local pro corpus, no cross-source personal memory, no offline, no evidence-grade provenance |
| ChessBase | A maintenance layer over Mega, sold into the existing base | 30 years of storage-shaped architecture and Windows-first gravity; price; open ecosystem goodwill |
| En Croissant / OSS bag of parts | A delta feature in a free toolkit | Productized trust: the review UX, the scoring function, the support and the business around the commons |

The honest read: if the wedge works, someone with more distribution attempts it inside 18 months. The defenses are exactly the &sect;3 list - habit, distribution, delta-model quality, trust record - which is why Bet 2 and the trust ramp are the company.

### The GTM consequence of the legal posture

S1's no-go branch and the app-store/&#8203;GPL conflict (&sect;12) share a consequence: **direct signed downloads ship first, with no store discovery.** For a &euro;5&ndash;8 consumer product, discovery then comes from community, content and word of mouth in exactly the forums this report's evidence base is drawn from. That is a slower, cheaper, more credible motion - and it is the one a small open business can actually run. It is stated here so the marketing plan is never written against distribution the company has decided not to have. v5 adds the coach/community GTM on top of that motion (&sect;10): coaches' rosters are the recruitment channel for exactly these communities, and titled-player experiences give the launch its credibility events.

<div class="pagebreak"></div>

# 12. Trust architecture and data rights

## The strongest part of v3, preserved - with two rows promoted from matrix to spike.

**AI may explain evidence; it is never itself the chess evidence.** Every factual chess claim links to a game, statistic, tablebase or reproducible engine run. Plans, tool calls and writes are visible, cancelable and replayable. Changing repertoire, annotations, training state or shared artefacts requires a diff and a scoped approval - with graduated standing approval earned per class under the &sect;9 trust ramp, and every automated write logged and reversible. The agent states uncertainty and preserves conflicting engines and sources. Cloud-model access is collection-scoped and off for sensitive preparation by default. Prompt injection inside PGNs, comments or imported documents is treated as data, never authority. Model adapters are conformance-tested for citation faithfulness, privacy and budget compliance.

**Sync is zero-knowledge.** The desktop core encrypts everything it syncs, client-side; the server stores and relays opaque ciphertext; the web/mobile surface decrypts locally with keys that never leave the user's devices. The approval gate executes on the client; no repertoire, game, note or plan ever reaches the server in plaintext, and the hosted control plane (&sect;10) - identity, billing, entitlements - sees account metadata only, content-blind by construction. A server breach is a ciphertext breach. The real risk is key loss on the user's side, handled with device-linked keys and an optional recovery phrase, documented before beta.

### Rights matrix - classified by confidence, two rows promoted

Four classes: **Verified fact** / **Interpretation** / **Requires counsel** / **Requires provider agreement**. The full matrix from v3 stands; two rows are no longer just rows:

| Connector / input | Classification | Status |
|---|---|---|
| ChessBase / Mega proprietary corpora | **Verified fact** (proprietary, annotated, paid): local indexing of the user's licensed copy only; anything more requires provider agreement | **Promoted to Spike S1** (&sect;4): the GTM headline rests on this row, so it is a week-one go/no-go, not an appendix-classified hope |
| App-store distribution of an AGPL/GPL desktop | **Requires counsel**: store terms/DRM can conflict | **Promoted to a GTM constraint** (&sect;11): direct signed downloads ship first; no store discovery is now a planning fact, not a footnote |
| Lichess dumps / broadcast games | **Verified fact**: CC0 / CC BY-SA 4.0, attribution carried as provenance | Unchanged |
| User's own PGN files and exports | **Verified fact**: user-provided, private by default | Unchanged - and now the primary onboarding input |
| Stockfish / Leela binaries | **Verified fact** (GPLv3 / GPL-3.0): unmodified UCI subprocesses where possible; notices + corresponding source when distributed; weights provenance tracked separately | Unchanged |
| UCI process separation | **Interpretation** &rarr; packaging decisions require counsel | Unchanged |
| Chess.com / site APIs (user's own games) | **Interpretation**; anything beyond per-user sync requires provider agreement | Back in V0.1 for the casual path (one-connect per-user sync); beyond per-user sync still requires provider agreement |
| TWIC weekly PGNs | **Interpretation**: local indexing of the user's downloads lower-risk; redistribution/curated feed requires counsel | Unchanged - primary delta feed is the user's own TWIC drops |
| Cloud model providers (BYOM) | **Requires provider agreement**: adapters expose retention/region/training terms; default off for prep | Unchanged - and the hosted control plane (&sect;10) is where entitlements live |
| E2E encrypted sync relay | **Internal infrastructure**: server stores user-encrypted blobs only; no provider content rights implicated | New in v5 - zero-knowledge sync, above |

The pipeline never flattens inputs into one rights-unknown pool: license-aware filters in search and agent tools, and exports fail closed when rights metadata is missing.

<div class="pagebreak"></div>

# 13. What would make this fail

## v3's table, plus the five failure modes the second critique named.

| Failure mode | Early warning | Countermeasure |
|---|---|---|
| **Segment without the pain** (new) | H0 fails; screen-shares show skipped maintenance and no guilt about it | Spike C + fork decision (&sect;4&ndash;5); re-segment or stop before building |
| **The model is not smarter than the dumb rule** (new) | Backtest margin &lt;15pp; beta control arm converges with the model arm | Spike B kill rule; permanent baseline control (&sect;7); Bet 2 dies honestly |
| **Noise death** (new) | Rising N, falling open rate, accept-rate collapsing toward rubber-stamping | Precision policy and per-user thresholds (&sect;7); N treated as a regression signal, never engagement |
| **Quiet-week abandonment** (new) | Users open busy weeks, skip quiet ones, drift away | Quiet week designed as a gain screen (&sect;9); quiet-week rate measured (&sect;6) |
| **Guilt churn** (new) | Exit interviews cite pressure/debt; unsubscribes cluster after stale-line surges | Gain framing (&sect;9); churn with exit reasons gated (&sect;6) |
| **Concierge confound** (new) | Beautiful retention that evaporates on automation | H2 restated: concierge is directional only (&sect;8) |
| **Incumbent fast-follow** (new) | Chessable/Lichess ship the 18-month rows (&sect;11) | Habit + delta quality + trust record; the &sect;3 defense list |
| **Sync trust breach** (new in v5) | Any plaintext content observable server-side; key-recovery complaints | Zero-knowledge sync (&sect;12): a server breach is a ciphertext breach; key recovery flow documented before beta |
| Wedge stays vague | Feature sprawl; weekly opens flat | H2/WTP gates; kill or re-wedge |
| Frankenstack inertia | Users import, nod, never return | Same-evening first value; PGN-drop onboarding (&sect;11) |
| Prediction overreach | One "probably" caught, everything distrusted | Evidence-only outputs |
| Opaque agent autonomy | Fluent uncited errors; silent writes | Typed tools, visible diffs, trust ramp with full log (&sect;9, &sect;12) |
| Treating data as free | Dubious corpus; stale games; EULA trouble | Rights matrix; S1 spike before the GTM headline ships (&sect;12) |
| "Open source" without migration | Users cannot leave | One-command complete export; independent readers - as trust, not moat (&sect;3) |
| One-maintainer bus factor | Release/security backlog | Paid maintenance around the commons; specs; narrow CLA keeps relicensing possible (&sect;10) |
<div class="pagebreak"></div>

# 14. Architecture and performance, reconciled

## The asymmetric split, budgeted honestly: heavy compute local, habit thin, sync zero-knowledge.

v4 reconciled the 16GB budgets with a local LLM by making the big orchestrator optional. v5 keeps that discipline and sharpens the split: **the desktop core carries the small local models, Stockfish and the indexer; the web/mobile surface carries nothing heavy; the server carries only encrypted blobs.** Per-user compute cost to the business is zero, because engines and models run on hardware the user already owns (&sect;10).

**Memory budget on the reference machine (4-core / 16GB), to be validated, not promised:**

| Resident | Budget | Notes |
|---|---:|---|
| OS + desktop | ~6 GB | Baseline |
| OpenChess app + UI | ~1.5 GB | |
| Position index + store cache | ~2 GB | 10m-game corpus; index size budget published with the benchmark |
| Stockfish (1&ndash;2 threads, hash) | ~1&ndash;2 GB | User-settable; verification runs are the default background job |
| Small local task models | ~1&ndash;2 GB | Quantized small models for classification, ranking, diagnosis and drafting - part of the default install; the engine remains the verifier (&sect;7) |
| Headroom | ~2&ndash;3 GB | Import bursts, browser, the user's life |
| Large LLM orchestrator | **Not resident by default** | Optional mode, below |

**The orchestration default is: small local models for bounded tasks** - mistake prediction, error classification, model-position retrieval, lesson ranking, weekly-plan drafting and delta explanations - **with engines and evals as verifier** (the forum research's bounded-target list, &sect;7). The heavy generative tier stays optional, exactly as v4 resolved it: hosted BYOM (user's own key, rights and retention terms exposed per provider, &sect;12) or an explicit local-model opt-in with its own published requirements (quantized 7&ndash;13B-class model, ~6&ndash;8 GB, recommended on 32GB machines), whose activation suspends the reference-machine p95 claims - the settings screen says so. The privacy trade is stated, not smoothed over: local mode keeps everything on-device at the cost of the budgets below; the default keeps the budgets and routes heavy drafting through the user's chosen provider or skips it. Detection, scoring, evidence, diagnosis, planning and training are all fully functional with no large LLM at all, because the LLM was never the chess evidence (&sect;12).

| Operation | Hypothesis | Benchmark definition (unchanged methodology: published harness, published corpus recipe, stated hardware, cold/warm cache separate, concurrency stated) |
|---|---|---|
| App open to usable workspace (desktop core) | &lt;2s warm, &lt;5s cold | Reference laptop, defined corpus resident, **no large LLM loaded** |
| Import 10,000 PGNs | &lt;60s, UI usable | Mixed-source messy corpus; background indexer active |
| Exact position search, 10m games | p95 &lt;250ms | Warm cache; single user + 1 background indexer |
| Header/player query, 10m games | p95 &lt;150ms | Cold-cache variant reported separately |
| Repertoire transposition lookup | &lt;50ms | 2k-position repertoire, worst-case fan-out |
| Relevance detection (scoring function) | &lt;5s/batch | 1k new games vs. full active repertoire, engine-drift component pre-computed nightly |
| Recurring-error diagnosis (weekly batch) | &lt;5 min for a 50-game week | Small local model classification + engine verification, background |
| Web/mobile review-queue load | p95 &lt;1.5s on 4G | Thin surface over decrypted local cache; sync pull in background |
| E2E sync push/pull (weekly package) | &lt;30s typical week | Encrypted blob relay; never on the review critical path; the server does zero chess compute |
| Orchestrator draft (hosted BYOM, optional) | &lt;60s, off critical path | Background batch; never blocks review |
| Crash recovery | No loss beyond last atomic edit | kill -9 torture suite at randomized write points |

Numbers from any other setup are marketing and will not be quoted.

<div class="pagebreak"></div>

# 15. Decision brief

## The order of operations is now the strategy.

**Fund first (weeks 1&ndash;3, ~3 weeks elapsed, no product code):**

1. **S2 - the delta backtest.** It prices Bet 2, the only bet that is also a moat. If the model cannot beat "new 2500+ game in your ECO, by recency" by 15 points, everything after this line is moot.
2. **S3 - the twenty H0 conversations.** They price the fork: maintenance-first or construction-first, and with it segment, onboarding, pricing and moat.
3. **S1 - the CBH/Mega go/no-go.** It prices the GTM headline. Knowing in week 1 that the pitch is "import your PGN folder" instead of "import your entire ChessBase archive" is worth the spike by itself.

**Then decide (&sect;5), with the spike results on one page. No evidence, no build.**

**Then fund the loop (fork weeks 1&ndash;12):**

4. **The concierge loop and thirty-user beta** on the fork-selected segment - PGN-drop import, recurring-error diagnosis, a three-item weekly plan, drills from the user's own positions, transfer check (maintenance branch: scored delta, diffed review, training) - with the parallel 2400+ probe, H3 as model-vs-baseline from day one, and the landing-page WTP test running in parallel.
5. **Only then, V0.1 productization:** the five capabilities - desktop core plus web/mobile habit surface, E2E encrypted sync, offline, crash-safe, full export - on the &sect;14 budgets.

What is deliberately not funded yet: account sync beyond the casual connect, native mobile, elite/team features beyond the 2400+ probe, CBH import beyond the spike. The project wins when "open" stops meaning assemble-it-yourself and starts meaning **maintained** - and, with the habit surface owned, **improved** - but it now finds out whether anyone's repertoire is there to be maintained or built, and whether its intelligence beats a dumb rule, *before* it builds the thing that maintains it.

<div class="pagebreak"></div>

# Appendix A - Competitive detail and the pricing warning

## Capability comparison (carried from v3)

| Product | Strongest job | What OpenChess should copy | Why it does not close the gap |
|---|---|---|---|
| ChessBase '26 + Mega | End-to-end professional prep with curated corpus | Reference search, player prep, update habit | Windows-only, expensive bundle, storage not maintenance |
| Lichess Study / Explorer | Frictionless browser study and open online data | Links, collaboration simplicity, open data | Not a local pro-scale personal workbench |
| Scid / Scid vs. PC | Powerful free database and engine toolkit | Search depth, tree, CQL/EPD seriousness | Interface and platform debt |
| ChessX | Cross-platform database basics | Familiar desktop portability | Little training, automation or maintenance |
| En Croissant | Modern cross-platform OSS toolkit | Engine/database downloads, online imports, repertoire SRS | Early on pro corpus, provenance, delta |
| Nibbler | Leela/engine analysis UI | Rich engine visibility | Not a personal/reference database system |
| BanksiaGUI | Engine play, tournaments, analysis | Engine control and visualization | Database/preparation is not the center |
| Cute Chess | Engine tournaments and protocol tooling | Headless tournament harness | Developer tool |
| ChessTempo | Opening training, tactics, searchable games | Repertoire scale and bulk analysis | Web subscription; not an owned workbench |
| Chessable | Course-led spaced repetition; **owns the target user's phone habit** | Low-friction recall loop | Course marketplace, not the user's research archive - but see the 18-month row, &sect;11 |
| Chessify | Cloud engines and professional databases | Provider abstraction | Subscription/coin economics; cloud dependency |

## Pricing benchmark - reframed as the warning it is (public pages, 18 September 2026; snapshots, may change)

Every entry below sells **content** - curated games, annotated courses, magazine archives. OpenChess sells none. These prices show what content commands, not what a maintenance service over the user's own content will bear. The honest comparator is productivity SaaS; the honest ceiling at this segment is &euro;5&ndash;8/month (&sect;10).

| Offer | Observed public price | What it actually tells us |
|---|---:|---|
| ChessBase '26 Premium package | &euro;349.90 | Software **+ Mega + services**: content carries the price |
| ChessBase '26 Mega package | &euro;499.90 | Correspondence database and magazine, again content |
| Mega Database 2026 | &euro;229.90 | A curated corpus alone commands this - we sell no corpus |
| Chessable PRO | $11.99/mo; $59.99&ndash;74.99/yr | Habit + content library; the closest subscription shape, with content attached |
| ChessTempo Silver / Diamond | $3/mo / $79/yr | **The honest band**: utility tooling, no content moat, at utility prices |
| Chessify dedicated compute | Coin-metered | Compute supports usage pricing; not our shape |
<div class="pagebreak"></div>

# Appendix B - The Spike B backtest protocol

## What the naive baseline scores, and whether a smarter model beats it, split by rating band - before any product exists.

**Question.** Replaying 2024&ndash;2025, how often would a surfaced delta have changed a decision that mattered - for the naive baseline, and for the &sect;7 scoring function - at each rating band?

**Data.** TWIC weekly PGNs and Lichess CC0/elite dumps for 2024&ndash;2025 (public, licensed for this use per &sect;12). Fifty real amateur repertoires: derived from the game histories of consenting club players recruited in the same communities as Spike C, plus anonymised public studies; a player's own game history defines their de facto repertoire where no explicit file exists. This doubles as an H0 probe: the recruitment itself measures how many players *have* an explicit repertoire.

**Replay.** Chronological weekly snapshots. At each week t, both rankers see only data &le; t. Each produces its top-5 surfaces per repertoire per week.

**Labels (decision-relevant).** A surfaced item is decision-relevant if, within four weeks of surfacing: (a) verified engine re-analysis at the user's tabiya shows the surfaced material changes the evaluation (&ge;0.3) or the practical choice in the user's line; or (b) a blinded human panel (labelled sample, n &ge; 300 items, band-matched experts) judges that a prepared player at that band should change something. Both labels reported; (a) is the primary, (b) the calibration.

**Metrics.** Precision@5 per user-week; decision-change rate per surfaced item; acceptance-at-30-days proxy (does the change persist in the repertoire's later state); all split by band: 1800&ndash;1999, 2000&ndash;2199, 2200&ndash;2399, 2400+ (aligned with the companion development plan's S2 task family).

**Kill / proceed rule.** Model must beat the baseline by &ge;15 percentage points of decision-relevance in the 1800&ndash;2200 bands. Otherwise Bet 2 dies: no twelve-week build; options are a no-moat digest product or stop (&sect;5, rule 4).

**Cost.** One engineer, two to three weeks, no product code, fully reproducible scripts and published corpus recipe.

**What it also buys.** The backtest corpus and harness become the permanent regression rig: every future scoring change replays this benchmark before it ships, and the beta's model-vs-baseline control arm (&sect;6) is the same comparison running live.

<div class="pagebreak"></div>

# Appendix C - Market sizing arithmetic

## Rough, honest, and stated so the instruments can correct it.

| Step | Assumption | Basis / instrument that tests it |
|---|---|---|
| Weekly active rated players, 1800&ndash;2300, worldwide (online + OTB-regular) | Low hundreds of thousands, order of magnitude | Public site statistics and rating distributions; deliberately not false-precise |
| Fraction who maintain a repertoire beyond an app course | 10&ndash;25% | **Spike C / H0** measures this directly |
| Addressable pool | Tens of thousands | Derived |
| Fraction who will pay &euro;8/$10/mo to improve | 3&ndash;8% | **Landing-page test** (&sect;10) measures checkout starts; prepay is secondary |
| Paying users, scenarios | 1,000 / 5,000 / 10,000 | Bear / base / optimistic |
| ARR at &euro;8/$10/mo | &euro;96k / &euro;480k / &euro;960k | Arithmetic; the 1,000-user bear row is the committed 2027 goal (&sect;10) |
| Free-to-paid conversion | 20% (hypothesis) | Beta conversion + checkout starts (&sect;10) |
| Visitor-to-free conversion | 5% (hypothesis) | Landing-page + onboarding funnel |
| Implied 2027 traffic for the 1,000-paying goal | ~100,000 visitors; ~8,000/month | Community, content and word-of-mouth GTM (&sect;11) |
| Primary acquisition channels | The free OSS tier itself, then coach champions, user referral, titled-player events | Channel-level conversion and cost measured from first campaign; caps fit the ~90% margin guardrail (&sect;10) |
| Cost to serve per user per month | Measured from beta week 1 | Sync storage + bandwidth + control plane + generative credits; compute stays on the user's desktop (&sect;6, &sect;14) |

**Conclusion, committed in &sect;10:** the goal is the bear row - 1,000 paying users at &euro;8/$10 during 2027, ~&euro;96k ARR - run as a small, durable business whose product is #1 in quality; not obviously a fundable one; the register of this document matches that. Revisited only if the fork resolves toward construction/coaching economics or the coach/team gates pass strongly.

<div class="pagebreak"></div>

# Appendix D - Scoring function working specification

## Starting values, freeze discipline, and the "show me the arithmetic" contract.

**Form.** S(line) = (w<sub>T</sub>&middot;T + w<sub>E</sub>&middot;E + w<sub>D</sub>&middot;D + w<sub>R</sub>&middot;R + w<sub>A</sub>&middot;A) &times; F &times; G, components normalised to [0,1].

**Starting weights (reference band 2200+), to be frozen by the Spike B backtest:**

| Component | Start | Normalisation sketch |
|---|---:|---|
| T - theory activity | 0.15 | Games this window in the line, quality-weighted, scaled against the line's own trailing-year baseline |
| E - elite adoption shift | 0.20 | Net elite move-switching at the tabiya, significance-guarded at low n |
| D - engine eval drift | 0.20 | |&Delta;eval| at the user's tabiya since last review, capped at 0.5, verified runs only |
| R - own results | 0.25 | Score in the line over trailing 90 days vs. user's overall score; suppressed below 5 games |
| A - age since review | 0.20 | Days since review, saturating at 12 months |

**Modulators.** F (line frequency): occurrences in the user's own games over the trailing year relative to their repertoire median, clamped [0.25, 2.0]. G (rating band): 2200+ as reference; at 2000&ndash;2200, T and E &times;0.6; at 1800&ndash;2000, T and E &times;0.4, with R and D renormalised upward. These curves encode the critique's central insight directly: elite fashion matters less as rating falls; your own results and drift matter everywhere.

**Threshold.** Per-user, precision-targeted. Cold start at the score distribution's 75th percentile, targeting &ge;60% decision-relevance; adjusted weekly on accept/reject feedback; floor and ceiling enforced so N stays in the &sect;9 budget.

**Discipline.** Weights frozen at backtest close; re-fit at most monthly; every change versioned and replayed against the Appendix B rig before shipping. Every surfaced item in the Inbox can expand its own arithmetic - the trust architecture requires the answer to "why did this surface?" to be addition, not attitude.

<div class="pagebreak"></div>

# Appendix E - Critique traceability (21 points, 18 Sep 2026)

## Where each point landed - in the appendix, deliberately, so the body can stay ranked by what kills the company.

*This is v4's table, preserved. v5's changes are owner-directed (architecture, business goal) and forum-research-driven (entry paths); the v5 change log is Appendix G.*

| # | Critique point | Where v4 answers it |
|---|---|---|
| 1 | Segment without the pain; H3 not behavioural | Killer 1 (&sect;3); H3 rewritten as behaviour with baseline control (&sect;6, &sect;8) |
| 2 | Onboarding assumes a repertoire most users lack | H0 + Spike C (&sect;4); the fork (&sect;5) |
| 3 | Moat contradicts openness | Killer 3 (&sect;3): defensibility restated; "Git for chess knowledge" retired as strategy |
| 4 | No scoring function, no baseline, no backtest | &sect;7, Appendix D; Spike B, Appendix B |
| 5 | False positives uncosted; empty inbox unplanned | Precision policy (&sect;7); quiet-week design (&sect;9); noise death (&sect;13) |
| 6 | Funnel ignores N | N inside the funnel with weekly budget table (&sect;9); H4 (&sect;6) |
| 7 | H2 confounded by concierge | H2 restated; concierge directional only (&sect;8) |
| 8 | Performance budgets contradict local-LLM architecture | &sect;14: local orchestration optional, budgets re-scoped, privacy trade stated |
| 9 | Missing metrics: retention, cost to serve, churn, OTB improvement | New gated metrics (&sect;6) |
| 10 | Content-business price anchors | Reframed as warning; honest ceiling &euro;5&ndash;8 (&sect;10, Appendix A) |
| 11 | No market sizing; company type unstated | Arithmetic + committed company type (&sect;10, Appendix C) |
| 12 | H5 too weak to carry WTP | Demoted; landing-page test primary (&sect;10) |
| 13 | Seven capabilities is a year of work | Cut to five; sync and SRS out; cut-order named (&sect;11) |
| 14 | Phase 0 contradiction; titled players as core study | Spikes before commitment (&sect;4&ndash;5); primary study 2000&ndash;2200 weekly players, titled secondary (&sect;11) |
| 15 | GTM headline rests on riskiest rights row | Spike S1 go/no-go (&sect;4); app-store consequence stated (&sect;11) |
| 16 | Incumbents treated as static | 18-month fast-follow table (&sect;11) |
| 17 | No mobile; training is a phone behaviour | Anki/Chessable export to phone day one (&sect;11) - **superseded in v5**: training is owned on the web/mobile habit surface; export remains a feature |
| 18 | Staleness surface is a guilt mechanism | Reframed to gain; quiet-week contract (&sect;9) |
| 19 | v3 organised as an even-weighted rebuttal | v4 ranked by kill order; this table lives in an appendix (&sect;1) |
| 20 | Hedged into believing nothing | Three bets stated flatly, with kill conditions (&sect;2) |
| 21 | Approval gate fights the value prop | Graduated trust ramp with logged, reversible auto-apply (&sect;9) |

<div class="pagebreak"></div>

# Appendix F - Research coverage, confidence and sources

## Same evidence base as v2 and v3, honestly bounded. v4 adds analysis, not new sources.

The v2 evidence base stands: **37 targeted search batches; 798 unique URLs across 162 domains discovered; 40 decisive pages deep-read** across official/vendor, OSS/technical and community sources. **v5 adds the forum research report of 18 September 2026** (`OpenChess-forum-research-2026-09-18`): 20+ pages across Chess.com forums, Lichess forums, Reddit/r/chess, expert interviews (Anand, Blohberger, Mendonca) and the official Lichess dataset pages, with its own inline source links. Search excerpts were used for discovery only, never for load-bearing claims. X coverage via public web indexing was weak; the report does not pretend inaccessible posts were read. The companion `OpenChess-2027-source-ledger-v3-b513e610.csv` remains the machine-usable ledger; v4's additions are the critique (Appendix E), the protocols (Appendices B, D) and the arithmetic (Appendix C) - all analysis layered on the same base.

**Confidence:** high on vendor offers/prices, public feature sets, open-project capabilities, protocol and license facts cited to first-party pages; medium-high on recurring community pain themes (self-selection acknowledged); medium on elite-team requirements (now evidence-gated); low-until-tested on willingness to switch and to pay (now instrumented, &sect;10). New in v4: the segment-pain assumption itself is downgraded from premise to hypothesis (H0), and the delta's superiority is an open empirical question until Spike B reports. New in v5: the forum findings shift the prior toward the build-and-train path but are treated as directional and self-selected - input to the spikes and the fork, never load-bearing prevalence; professional identities on forums are hard to authenticate, and direct X posts were not reliably accessible in that pass.

**Core verified sources (carried from v3):** ChessBase '26 product and shop pages (cb26.chessbase.com; shop.chessbase.com - Premium &euro;349.90, Mega package &euro;499.90, Mega Database 2026 &euro;229.90); ChessBase 18 help; En Croissant (encroissant.org; github.com/franciscoBSalgueiro/en-croissant); Scid vs. PC; BanksiaGUI; Nibbler; Cute Chess; ChessX; Lichess (github.com/ornicar/lila; database.lichess.org incl. broadcast CC BY-SA 4.0); PGN/UCI/Syzygy references (chessprogramming.org); community evidence across r/chess, r/ComputerChess, lichess.org forums, TalkChess and Chess StackExchange (18&ndash;27 in the v3 numbered list); Chessify pricing; ChessTempo memberships and database; Chessable PRO pricing; Stockfish and Leela Chess Zero sources (GPLv3 / GPL-3.0); GNU GPL FAQ and AGPLv3; Creative Commons CC0 and CC BY-SA 4.0. Full URLs preserved in the v3 report and ledger.

<div class="pagebreak"></div>

# Appendix G - v5 change log and forum fold-in

## What changed from v4 to v5, where, and why. Everything else in v4 stands.

v5 is five owner-directed changes and one research fold-in on top of v4's skeleton. The kill-order structure, the three bets, the spikes, the fork mechanics, the trust ramp, the pricing instruments, the rights matrix and Appendices A&ndash;F are kept intact except where this table says otherwise.

| # | Change | What it replaced | Where it landed |
|---|---|---|---|
| 1 | **Asymmetric architecture** - local-first desktop core (local models + Stockfish + indexer + delta engine; private by default; zero per-user compute cost) plus a thin web/mobile habit surface (review queue, approval gate, quiet-week screen, training), joined by E2E encrypted sync | The export-first phone story ("training ships as Anki/Chessable export, on the phone, day one") | Cover; &sect;1; &sect;9 (ambition, quiet week, trust ramp gate on web, funnel); &sect;10 (cost to serve); &sect;11 (V0.1 scope, phone paragraph, sequencing); &sect;12 (zero-knowledge sync); &sect;13 (sync trust breach); &sect;14 (budgets); &sect;15 |
| 2 | **Training is owned** - the product ambition is "the only platform a serious amateur improves on"; Anki/Chessable export remains a feature, not the training story | Export as the entire mobile/training strategy | &sect;9; &sect;11 (V0.1 item 5, phone paragraph); Appendix E row 17 (marked superseded) |
| 3 | **Committed business goal** - 1,000 paying users at &euro;8/$10 per month during 2027 (~&euro;96k ARR); the funnel derived backwards (100k visitors &rarr; 5k active free users &rarr; 1k paid; free-tier framing added by change 7); generative AI + Lichess CC0 data named as first-class benefit levers; &euro;8 held inside the honest ChessTempo band | Scenario-only ARR framing with no committed target | Cover; &sect;1; &sect;10 (goal, funnel, benefit levers, price-band framing); Appendix C (funnel rows) |
| 4 | **Forum research fold-in** (18 Sep 2026 report) - two entry paths on one position-native memory layer: "build and train from my games" (1800&ndash;2300 primary; construction first-class, since most targets have no importable repertoire) and "maintain and triage my repertoire" (2400+); the diagnosis-to-plan loop: PGN &rarr; recurring-error diagnosis &rarr; three-item weekly plan &rarr; drills from the user's own positions &rarr; transfer check; artifact classification for H0; parallel 2400+ probe | Fork branches phrased as abstract maintenance-vs-construction; a maintenance-only concierge loop | &sect;2 (Bet 1); &sect;3 (killer 1); &sect;4 (S3); &sect;5 (fork); &sect;6 (concierge loop, H0, beta probe); &sect;7 (diagnosis note); &sect;9 (paths); &sect;15; Appendix F |
| 5 | **Coach and community GTM** - chess coaches (mostly former players) as product champions: a coach dashboard over students' recurring-error themes, weekly plans and transfer checks, plus revenue share on referred subscribers; a user referral scheme; paid titled-player experiences (simuls, play-a-titled-player sessions) as both revenue and marketing. Coaches' rosters solve beta recruitment. Everything sized inside the committed &euro;8/$10 price and the ~90% gross-margin guardrail | Organic community motion only, no named channels | &sect;10 (new subsection); &sect;6 (beta recruitment); &sect;11 (GTM paragraph); Appendix C |
| 6 | **Casual path: auto-import + optimal sessions** - Lichess/Chess.com account sync moves from the V0.2 cut into V0.1 for the casual path (one-connect OAuth, games flow in automatically); the casual product is targeted, science-backed sessions (spaced retrieval, bounded weekly decisions, adaptive difficulty) sized for a lazy player to finish, not hours of pro-style prep. Two personas stated flatly: pro path = PGN/CBH import + hours; casual path = auto-import + optimal minutes | "Account sync cut to V0.2" as a blanket cut | &sect;1; &sect;9 (personas, funnel); &sect;11 (V0.1 item 1, out-list); &sect;6 (H1, beta onboarding); &sect;12 (rights matrix row); &sect;15 |
| 7 | **Free tier as marketing; paid = infra + credits** - the free OSS tier is genuinely great (the full local loop: import, memory, diagnosis, plan, drills, training, review) and is the entire top of funnel, positioned as the best free chess harness and coach, open source; the &euro;8/$10 payment buys hosted sync across devices, hosted/bigger models, a monthly generative-credit bundle and coach features - mostly infrastructure plus bundled LLM credits. The funnel is re-read as visitor &rarr; active free user &rarr; paid, and the generosity-vs-conversion tension is named, with the 20% conversion re-read as free-to-paid | Free tier implied by the OSS packaging but never named as the funnel; the funnel ran on expiring 'trials' | &sect;10 (funnel, new subsection, benefit levers, instruments, GTM); &sect;1; &sect;6 (cost-to-serve row); Appendix C (funnel rows) |

**Superseded v4 statements, explicitly:** "training ships as Chessable/Anki export, on the phone, day one" (v4 &sect;1); "On the phone, day one&hellip; the export targets AnkiDroid/mobile Chessable from the first beta build" (v4 &sect;11); "a built SRS" listed as unfunded (v4 &sect;15) - training is now owned; scenario-only ARR with no committed target (v4 &sect;10). Everything else in v4 stands.

### The forum research, in five lines

1. Professionals and very serious players: ChessBase is powerful but labor-heavy and brittle; the hard job is deciding which new game or engine idea changes a real decision (Anand: players are "flooded with information").
2. Casual through strong club players: the recurring pain is turning games, puzzles, videos and courses into a coherent plan, remembering what was studied, and fixing errors that recur - many need to **build** a repertoire from scattered PGNs, Studies and course chapters.
3. The strongest opportunity is one position-native memory and diagnosis layer with two entry paths - not a generic chatbot or another engine-explanation screen.
4. Small models earn their keep on bounded tasks - mistake prediction, error classification, model-position retrieval, lesson ranking, weekly-plan optimisation - with engines and evals as verifier; theory alerts below the segment where currency binds are mostly noise.
5. Lichess CC0 games, puzzles and evals are a cost and distribution wedge - open data, indexed locally; defensibility comes from calibrated personalization, measured transfer, habit and distribution, while user memory stays portable.

<p class="colophon">OpenChess 2027, v5 &middot; 18 September 2026 &middot; working name, final name pending &middot; produced as a single integrated document; PDF and searchable Markdown versions are the same content.</p>
