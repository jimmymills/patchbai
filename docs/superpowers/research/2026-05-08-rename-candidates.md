# Rename candidates after the patchbai.com conflict

**Date:** 2026-05-08
**Status:** Research — recommendation only, no code changes proposed.
**Why:** `patchbai.com` is taken by an unrelated thing. The current PyPI / GitHub
namespace (`jimmymills/patchbai`, `pipx install patchbai`) is technically still
clear, but if we're going to rename, now — before 1.0 and before the README's
brand voice gets entrenched any further — is the right time.

---

## What this thing is, today

> A Textual TUI that lets you run several Claude Code agent sessions
> side-by-side under a single conversational *orchestrator* agent. The
> orchestrator spawns/supervises children, owns a declarative panel layout it
> reshapes on command (atomic diff-and-swap with rollback), routes children's
> permission requests through Textual modals, persists everything (workspaces,
> themes, layouts, transcripts), and can author and ship new Python widgets at
> runtime via MCP tools. Children can talk back through `notify_orchestrator`
> and `ask_orchestrator`. It's "the room you wish you had" for parallel
> agentic work — a tiling manager whose tiler is itself a Claude agent.

That's the brand to name. Notable surfaces a name has to live on:

- A short CLI verb (`mt` is currently the alias). Long names hurt here.
- A Python package on PyPI (`pipx install <name>`).
- A repo URL — `github.com/jimmymills/<name>` per `pyproject.toml`.
- A README headline ("**\<Name\> is the room you wish you had.**").
- A screencast / showoff title ("I built \<name\>", "check out \<name\>").

---

## Methodology and caveats

**Availability checks were live HTTP fetches** against PyPI, GitHub, and the
relevant TLDs on 2026-05-08. Specifically:

- **PyPI**: `https://pypi.org/project/<name>/` — 404 = unclaimed.
- **GitHub user/org**: `https://github.com/<name>` — 404 = unclaimed.
- **GitHub repo under jimmymills**: `https://github.com/jimmymills/<name>` —
  404 = available as a repo name there.
- **TLDs**: `https://<name>.com/`, `.dev/`, `.io/` — interpreted cautiously.

**A 200 on a TLD does not mean "for sale" — it means there's a server
answering, which usually means the domain is owned.** A 404 / parked-page /
NXDOMAIN-like response means *probably available, but only WHOIS is
authoritative.* Several TLD checks returned `ECONNREFUSED`, blank HTML, or
timeouts — those are flagged `?` in the matrices below, not `✓`.

I also did **prior-art GitHub searches** on the candidates I cared about, to
catch dev-tool collisions that pure namespace-availability checks would miss
(the trap that previously got us into "patchbai is fine on PyPI, oh wait
patchbai.com is unrelated-thing").

---

## Brainstorm — 20 candidates across vibes

Each comes with a one-line rationale and a gut check on (M)emorability,
(P)ronounceability, and (S)ound-in-context. Scale: ✓ ✓✓ ✓✓✓.

### Conducting / orchestration metaphor (already in the README's voice)

| Name | Rationale | M | P | S |
|---|---|---|---|---|
| **Maestra** | The agent that conducts multiple instrumentalists. Italian feminine, distinct from the more-claimed "maestro". | ✓✓✓ | ✓✓✓ | ✓✓ |
| **Podium** | Already used in the README ("you tell it from the podium"). Conductor's stand. | ✓✓✓ | ✓✓✓ | ✓✓ |
| **Chorale** | A chorus of voices in harmony, conducted. Pretty. Slightly archaic. | ✓✓ | ✓✓ | ✓✓ |
| **Cantor** | The lead singer in a choir or service. Short, punchy. | ✓✓ | ✓✓✓ | ✓✓ (math collision: Georg Cantor) |
| **Ostinato** | A repeated musical motif — the orchestrator's heartbeat. Obscure. | ✓ | ✓✓ | ✓ |
| **Concerto** | A solo (orchestrator) plus orchestra (children). Heavily-used word. | ✓✓ | ✓✓✓ | ✓ |

### Room / gathering / assembly metaphor (the README's "the room you wish you had")

| Name | Rationale | M | P | S |
|---|---|---|---|---|
| **Atrium** | The central open courtyard everything else opens onto. README-on-brand. | ✓✓✓ | ✓✓✓ | ✓✓✓ |
| **Foyer** | The small front room. Humble. Slightly French-spelling-trap. | ✓✓ | ✓✓ | ✓✓ |
| **Plenum** | A *full assembly* — every agent in attendance. Slightly archaic, distinctive. | ✓✓ | ✓✓ | ✓✓ |
| **Vestry** | Small council chamber. Archaic, very distinctive. | ✓✓ | ✓✓ | ✓ |
| **Belfry** | The watchtower room. Orchestrator perches above the children. | ✓✓✓ | ✓✓✓ | ✓✓ ("bats in the belfry") |
| **Lyceum** | Aristotle's school — many thinkers under one roof. | ✓ | ✓✓ | ✓ |
| **Atelier** | Multi-craftsperson workshop. Heavily UX/design overloaded. | ✓✓ | ✓ | ✓ |

### Patches / mosaic / tile metaphor (the OG "patch + bai" inspiration)

| Name | Rationale | M | P | S |
|---|---|---|---|---|
| **Tessera** | Latin for the small tile in a mosaic — each panel literally is one. | ✓✓✓ | ✓✓✓ | ✓✓✓ |
| **Tessellate** | Verb-form of the same. Longer, more technical-sounding. | ✓✓ | ✓✓ | ✓ |
| **Quilter** | Stitches patches into one fabric. Literal callback to patchbai. Cute. | ✓✓ | ✓✓✓ | ✓ |
| **Lattice** | A grid you arrange things into. Common dev-tool name (collision risk). | ✓✓ | ✓✓✓ | ✓✓ |

### Multi-agent / many-things-gathered (the children dimension)

| Name | Rationale | M | P | S |
|---|---|---|---|---|
| **Rookery** | A colony where many smart corvids gather. Memorable, on-theme, distinctive. | ✓✓✓ | ✓✓✓ | ✓✓✓ |
| **Aviary** | Where many birds live together. Softer than rookery. | ✓✓ | ✓✓ | ✓✓ |
| **Murmuration** | The collective swirl of starlings. Beautiful, but seven syllables. | ✓✓ | ✓ | ✓ |
| **Skein** | A flock of geese in flight; a tangled bundle organized into one. **Burned: PyPI taken (Apache YARN deploy tool, real dev-tool collision).** | — | — | — |

---

## Verified availability — top 8 shortlist

I checked all six namespaces for the eight strongest candidates above. Results
as of 2026-05-08, in approximately decreasing order of brand cleanness.

Legend: ✓ = available, ✗ = taken / collision, ? = ambiguous response (timeout,
empty HTML, ECONNREFUSED — domain may or may not be registered; WHOIS would
settle it).

| Name | PyPI | GH user/org | jimmymills/`<name>` | .com | .dev | .io | Dev-tool prior art |
|---|:-:|:-:|:-:|:-:|:-:|:-:|---|
| **Rookery** | ✓ | ✗ (10-repo dormant org, last active years ago) | ✓ | ? blank | ? timeout | ? blank/timeout | **None notable.** Top GitHub hits are tiny (≤30 stars). |
| **Belfry** | ✓ | ✗ (empty user) | ✓ | ✗ parked (`info@belfry.com`) | ? ECONNREFUSED | ✗ TLS-cert mismatch (registered) | **`BelfrySCAD/BOSL2` — 2.2k stars** OpenSCAD library. Different domain (3D CAD), but recognizable. |
| **Chorale** | ✓ | ✗ (empty user) | ✓ | ✗ for-sale (synergytech mkt) | ✗ 404 (registered) | ? timeout | **One Rust toolkit, ~167 stars**, for Notion. Mild. |
| **Plenum** | ✓ | ✗ (empty user, 1 follower) | ✓ | ✗ owned by Telepathy.com (premium-domain investor) | ✗ Squarespace parked | ? timeout | **`indy-plenum` on PyPI** — Hyperledger Indy BFT consensus library. Different namespace but the bare word is taken by a known project. |
| **Tessera** | ✓ | ✗ (org, 1 small repo from 2014) | ✓ | ✗ redirects to Adeia.com (semiconductor IP, owns Tessera trademark) | ✗ for-sale (Spaceship) | ? ECONNREFUSED | **ConsenSys `tessera` — 195 stars**, Quorum private-tx manager (archiving June 2026). **Plus** Adeia trademark in semiconductor IP. Heavy. |
| **Maestra** | ✓ | ✗ (empty org) | ✓ | ? parked / "coming soon" | ✗ for-sale (Spaceship) | ✗ active SaaS (`maestra.io` = DTC personalization platform with G2 reviews) | Plus widely-used **Maestra.ai** dubbing/transcription product (AI-adjacent — direct collision). |
| **Atrium** | ✓ | ✗ (empty user) | ✓ | ✗ active (Atrium Windows & Doors) | ? ECONNREFUSED | ✗ active (Cardano blockchain product) | Multiple dev-adjacent prior uses. |
| **Podium** | ✓ | ✗ **(active 25-repo Elixir-focused org)** | ✓ | ✗ active **($3B+ SaaS, 100k+ businesses)** | ✗ active (digital agency) | ✗ active (PODIUM — AI architecture/BIM platform, ISO certified) | The single worst collision profile in the shortlist. |

### How to read this

- The **GitHub user/org** column being ✗ is essentially universal for any
  one-syllable English word — those got squatted years ago. It only matters
  if the squatter is *active*; for our purposes the practical question is
  whether `github.com/jimmymills/<name>` is free, and for all 8 it is.
- The **.com** column being ✗ is also nearly universal. The interesting
  question is whether it's owned by an *active dev-adjacent product* — for
  most of these it's not, but **Podium.com is a $3B SaaS** and that's a hard
  no on its own.
- The **dev-tool prior art** column is what burned `patchbai`'s domain (a
  real product owns `patchbai.com`). Our PyPI namespace is fine — but a
  popular GitHub project with the same name will show up in every search and
  hurt SEO/disambiguation.

---

## Top 3 recommendations

### 1. Rookery — the cleanest pick

| Namespace | Status |
|---|:-:|
| PyPI `rookery` | ✓ |
| `github.com/rookery` (top-level org) | ✗ (dormant org, 10 stale repos) |
| `github.com/jimmymills/rookery` | ✓ |
| `rookery.com` | ? blank/empty response (probably parked, WHOIS to confirm) |
| `rookery.dev` | ? timeout (likely unregistered) |
| `rookery.io` | ? blank/timeout |
| Notable GitHub project named "rookery" | None ≥ 30 stars |

**Why it fits.** A rookery is a colony of rooks — corvids, the smartest
birds, famous for working in coordinated groups. Each child agent is a rook;
the orchestrator is the senior bird that watches the whole tree. It maps
neatly onto the README's framing ("one TUI, N parallel sessions") and onto
the visual fact that the AgentTable is *literally a row per agent in one
shared roost.* Pronounceable in one beat, easy to type, distinct in search.
"I built rookery" / "check out rookery" both sound good.

**Hazards.** GitHub top-level org `github.com/rookery` is held by a dormant
org with 10 mostly-forked repos from years ago — annoying but irrelevant
since we'd live at `jimmymills/rookery`. The TLD checks were ambiguous
rather than green; before committing the rename, run `whois rookery.com /
.dev / .io` to confirm. No notable dev-tool collisions in GitHub search,
which is the rare green signal.

### 2. Chorale — clean if you want music vibes

| Namespace | Status |
|---|:-:|
| PyPI `chorale` | ✓ |
| `github.com/chorale` (user) | ✗ (empty profile, no repos) |
| `github.com/jimmymills/chorale` | ✓ |
| `chorale.com` | ✗ explicitly listed for sale (synergytech marketplace) |
| `chorale.dev` | ✗ 404 (registered, no site) |
| `chorale.io` | ? timeout (ambiguous) |
| Notable GitHub project named "chorale" | One ~167-star Rust Notion toolkit |

**Why it fits.** A chorale is a multi-voice harmonic piece, conducted. Direct
metaphor for the orchestrator + N children, in the same musical-score family
the README already invokes ("conductor", "podium", "ensemble"). Slightly more
elegant than Rookery; says "this is a thoughtful coordination tool" rather
than "this is a flock of agents". The "-ale" ending is mildly distinctive.

**Hazards.** There's a Rust toolkit named Chorale (~167 stars) for Notion
content — mild SEO collision but a totally different domain (Rust crate vs.
Python TUI) and small enough that this name would quickly out-rank it.
`chorale.com` is parked-for-sale on a marketplace, which means buying it
costs real money. Pronunciation "kuh-RAL" vs "KOR-ale" is a minor split (the
former is correct/musical; the latter is the corral/horse spelling people
sometimes confuse with). Slightly more syllables and slightly less
pronounceable than Rookery.

### 3. Plenum — clean and on-brand if you can live with one Hyperledger collision

| Namespace | Status |
|---|:-:|
| PyPI `plenum` | ✓ |
| `github.com/plenum` (user) | ✗ (empty user, 1 follower) |
| `github.com/jimmymills/plenum` | ✓ |
| `plenum.com` | ✗ owned by Telepathy.com, a known premium-domain investor (= expensive to buy) |
| `plenum.dev` | ✗ Squarespace placeholder (registered, no site) |
| `plenum.io` | ? timeout (ambiguous) |
| Notable project named "plenum" | **`indy-plenum`** — Hyperledger Indy BFT consensus library, on PyPI as `indy-plenum`. The bare word "Plenum" is the project's spoken name. |

**Why it fits.** A plenum is a "full assembly" — every member present,
deliberating together. That's exactly what the orchestrator runs: every
child agent in attendance, every transcript on the bus, every event
visible in one feed. Five letters, two syllables, distinctive. It also has
a faint architectural-engineering second meaning ("plenum chamber" — the
shared volume above a drop ceiling that *all the air flows through*),
which accidentally describes the EventBus.

**Hazards.** `indy-plenum` is a real Hyperledger project. It's namespaced
under `indy-` on PyPI so we wouldn't *literally* collide, but if anyone in
the SSI/DID/blockchain world hears "Plenum" they'll think of that first.
The collision is in a different community (cryptocurrency / decentralized
identity vs. agentic-dev-tools), but it'll show up in disambiguation. Also,
Telepathy.com owning the `.com` means buying that domain is ~5 figures if
they sell at all. If you don't care about the `.com` and only want the
`.dev` or `.io`, Plenum is fine — but you'd be the second Plenum on PyPI
adjacent in tag-cloud space.

---

## Runners-up explicitly **not** in the top 3, for the record

- **Tessera** — beautiful metaphor (mosaic tiles ≈ panels) but **two**
  meaningful tech prior-uses: ConsenSys's archived-but-real Quorum tx
  manager *and* Adeia's semiconductor-IP trademark on the word. Combined
  trademark + SEO risk too high for a public-launched dev tool.
- **Belfry** — would have been #2 if not for `BelfrySCAD/BOSL2`'s 2.2k
  stars. The CAD library and an agentic TUI live in different worlds, but
  "Belfry" Google searches will be split, and BOSL2 has the SEO incumbency.
- **Atrium** — domains heavily owned (Windows brand on `.com`, Cardano
  product on `.io`); near-impossible to be "the Atrium of dev tools".
- **Maestra** — collides with `maestra.io` DTC SaaS *and* Maestra.ai
  dubbing/transcription. AI-adjacent product collision is the worst kind.
- **Podium** — fully unusable: $3B SaaS owns the brand, and there's also a
  25-repo active GitHub org plus a separate AI architecture-software
  product on `.io`. **Hard pass.**
- **Skein** — was a top-tier metaphor (geese in flight, threads woven
  together) but **PyPI is taken** by a real Apache YARN deploy tool (Jim
  Crist-Harif's `skein`, ~v0.8). Single PyPI hit was enough to drop it
  before further checks.

---

## If I had to pick one tomorrow

**Rookery.** It's the only candidate in the shortlist with no notable
dev-tool collision *and* a free PyPI name *and* a free repo path under
`jimmymills/`. The metaphor is on-brand (many smart agents under one roof,
watched by a senior one), it's fun to say, and it survives the pitch sentence
("Rookery is the room you wish you had — one orchestrator, N Claude
sessions"). Run `whois` on `rookery.com / .dev / .io` to nail down the TLDs
before committing the rename, but on every other axis it's the cleanest pick.

---

## Things I did NOT verify (be honest about gaps)

- **WHOIS on any TLD.** All TLD signal is HTTP-only. A `?` in the matrix
  could collapse to "registered to a squatter who is willing to sell" or
  "available right now for $12" — only WHOIS knows.
- **Trademark searches.** None of these names were checked against USPTO or
  EUIPO. "Plenum" and "Tessera" are both English/Latin words used in many
  industries; serious commercial use (book deal, paid SaaS) would warrant a
  proper trademark search.
- **Pronunciation in non-English locales.** Rookery's "rook" → English chess
  piece; in some languages the cognate is the same bird. Chorale is French-
  borrowed and travels well. Plenum is Latin and travels well.
- **App store / npm / crates.io / Docker Hub namespaces.** Patchbai is
  Python-only today, so PyPI was the priority; if a Rust port is on the
  roadmap, also check `crates.io/crates/<name>`.
