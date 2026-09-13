# aislop — adoption evaluation

**Tool:** [scanaislop/aislop](https://github.com/scanaislop/aislop) v0.16.1 (MIT, Node ≥ 20)
**Evaluated:** 2026-09-13 against `oh-brother`, first at `9a2f7e5` then re-baselined at `815aa51`
**Question:** does it help limit AI-slop in this project, and in what role?

---

## 1. What it is

Deterministic static analysis (regex + AST + wrapped standard tooling) that scores a repo 0–100.
**No LLM in the runtime path** — same code in, same score out. Six engines run in parallel:

| Engine | Backed by | What it looks for |
|---|---|---|
| format | ruff, Biome, gofmt, … | formatting drift |
| lint | ruff, oxlint, clippy, … | language-level issues |
| code-quality | AST + knip | function/file size, nesting, dead code, unused deps |
| **ai-slop** | own rules | narrative/meta comments, swallowed exceptions, hidden fallbacks, `as any` casts, duplicated dispatch, TODO stubs, generic names |
| security | own rules + dep audits | eval/innerHTML/injection, hardcoded secrets |
| architecture | opt-in | import bans, layering |

Companion surfaces: `aislop fix [--safe|--dry-run]`, `aislop ci` (JSON + gate), `aislop scan --changes|--staged`,
inline suppression (`# aislop-ignore-next-line <rule> -- reason`), `.aislop/config.yml`, `.aislopignore`,
GitHub Action, SARIF output, and `aislop agent` (drives a local coding agent in a worktree). MIT, free CLI.

## 2. Measured on this repo (not the README's claims)

Read-only — no repo files were created or modified by a scan.

| Run | Score | Findings | Composition |
|---|---|---|---|
| Default rules, tree `9a2f7e5` | **25** ("Critical") | 84 | 56 = one rule (`ruff/UP031`, `%`-formatting) vs this project's deliberate house style; 7 = `ruff/FURB167` (`re.M` → `re.MULTILINE`), classified as **errors**; remainder small |
| Project config (three rules off), tree `9a2f7e5` | **47** | 20 | 16 style-policy, 4 ai-slop-indicator, 0 confirmed defects, 0 security |
| Project config, tree `0ccd1f0` (post packet 1) | **47** | 20 | identical rule multiset to the row above |
| Project config, tree `815aa51` (post packet 2, with the 3 suppressions below) | **60** | 17 | 16 style-policy, 1 ai-slop-indicator, 0 errors |

Performance: 86–110 ms for 3 files. `security`: 0 findings, always.

**Both packets introduced zero findings.** `9a2f7e5` → `0ccd1f0` (packet 1: the ASCII boundary guard
plus 85 new test lines): 20 findings on both sides, same rule multiset, score 47 both sides — every
apparent difference is a line-number shift from the added `import io` and the guard. Packet 2's own
`--changes` run after its diff: score 50, `confirmed-defect` 0, `conservative-security` 0, no category
that was not already present. Two data points, both clean, which is the first real evidence for the
"guard on incoming agent-authored code" role this document recommends.

### The AI-slop engine's hits, checked by hand

| Hit | Verdict |
|---|---|
| `ai-slop/swallowed-exception` `oh_brother.py:437` (`_remove_quietly`: `except OSError: pass`) | **False positive** — the docstring says "Best-effort file removal that never masks the original error". Deliberate |
| `ai-slop/swallowed-exception` `:914`, `:936` (upload handlers that print and continue) | Deliberate by design: the handlers print, and the post-upload version verification is authoritative (R9 in AGENTS.md). Not silently swallowed |
| `ai-slop/python-repetitive-dispatch` `:159` (5 sequential `if name == …` on SNMP key/value rows) | **False positive** — rewriting as a dispatch table would be less readable |
| `ai-slop/meta-comment` `:1009` | Cosmetic; a comment on an exit path |

The one genuinely useful signal in the default run was convergence with this session's audit rather
than anything new:

- `code-quality/complexity/function-too-long` at `:652` — the 293-line `update_firmware()` (audit §4, deliberately not split).
- `code-quality/complexity/function-too-long` at `:996`, `:57`; `file-too-large`.
- `lint/ruff/BLE001` (blind `except`) at `:504`, `:556`, `:1176` and `ruff/PLW0602` (`global` declared, never assigned) at `:653` — the same class as audit finding P8 (`_query_printer_version` catching `(Exception, SystemExit)`) and the module-globals decision.
- `lint/ruff/PIE810` at `:357`, `I001` at `:17`, `PLW1510` in the checker — real but trivial.

It found **nothing** about the em-dash/exit-code laundering, SIGTERM/SIGHUP, the 66-minute in-flight
window, the unconditional `os.replace`, the CWD-relative download, or the inlined `timeout=30` /
`MIN_FIRMWARE_SIZE`. It also cannot know this project's own rules (the named-constant convention, the
frozen exit-code contract).

## 3. Verdict

**Adopt it as a guard on incoming agent-authored code, not as a detector of past slop and not as a raw score gate.**

- Against hand-hardened code it is weak: 0 true positives across 84 findings, and its headline severity
  labels are unreliable (7 `re.M` readability preferences reported as *errors*; a documented
  `except OSError: pass` reported as a swallowed error). A repo with 137 tests, a drift checker and a
  byte-identical licence header scored "Critical" at 25, mostly for using `%`-formatting.
- As a **net for code an agent wrote this session** it is well suited: deterministic, ~90 ms, no LLM, no
  network, read-only, `--changes`/`--staged` scoping, and an inline suppression mechanism with a required
  reason. Both packets in this session passed it clean, which is the evidence the role needed.
- Its own gate, however, was **not usable as configured** until the three error-severity findings above
  were addressed — see §4, which is the part the first draft of this evaluation got wrong.

## 4. The gate (measured, not inferred)

Exit-code logic, read from `dist/cli.js` (`src/commands/scan-exit-code.ts`) — it is documented nowhere
in `--help`:

```js
const computeScanExitCode = (opts) => opts.hasErrors || opts.scoreable && opts.score < opts.failBelow ? 1 : 0;
```

**`ci` fails on either of two independent conditions: any error-severity finding (regardless of score),
or score < `failBelow`.** Measured:

| Configuration | exit | Why |
|---|---|---|
| `failBelow: 45`, tree as it was | **1** | 3 ai-slop errors present — the score was never consulted |
| `failBelow: 10` | **1** | same |
| `failBelow: 99` | **1** | same |
| clean fixture project | **0** | no findings at all |
| `failBelow: 45` after clearing the three errors | **0** | no errors, score above the ratchet |

So `ci.failBelow: 45` was **inert** while those three findings existed, and this document's earlier claim
that the gate "fails any new finding" was wrong as written: as configured it would have failed **every**
pull request that touches `oh_brother.py`. `--changes` scopes to changed **files**, not changed lines —
measured: an unchanged `scripts/check_agents_md.py` dropped out entirely (20 → 15 findings) while every
pre-existing finding in the touched files, all three errors included, still counted. A PR adding nothing
but a comment to `oh_brother.py` fails the gate. Also from the schema: `ci.format` accepts only `"json"`,
and the upstream default `failBelow` is `70` (the `--strict` init writes `85`).

### Three ways to clear it, all measured

| Path | errors | score | findings kept | `ci --changes` |
|---|---|---|---|---|
| baseline | 3 | 47 | 20 | exit 1 |
| **(a) per-site `aislop-ignore-next-line` directives — applied** | **0** | **60** | 17 | **exit 0** |
| (b) `rules: ai-slop/swallowed-exception: "off"` | 0 | 60 | 17 | exit 0 |
| (c) `rules: ai-slop/swallowed-exception: warning` | 0 | 55 | 20 | exit 0 |

**(a) was chosen.** (b) and (c) permanently blind a rule that should stay at error severity for code an
agent writes next; the directive exempts exactly the three documented sites and leaves the rule armed
everywhere else. (a) also costs three comment lines against a licensed fork's source, which is the only
argument for (c) — the original reason this was deferred.

Mechanism, read from `dist/cli.js` (`src/utils/suppress.ts`), not from docs:

- `next-line` targets **exactly** `directive_line + 1` (`addLine(i + 2)`), matched on `diagnostic.line`.
  The directive must sit immediately above the flagged line.
- Matching is rule equality or `rule.endsWith('/' + token)`; everything after `--` is ignored for
  matching, so the reason is free text for reviewers.
- **Pitfall, hit twice in one session:** a reason wrapped onto a second comment line moves the
  directive's target onto that comment and suppresses nothing, silently. The rule "single line or it is
  a no-op" is worth mechanising — the applier asserts `count("\n") == 1` and validates every directive
  target before writing.
- The directive works on all engines, not just ai-slop. `.aislopignore` is the file-level alternative.

The ratchet stays honest at 45: 60 > 45 passes, and the upstream default of 70 would still fail
(60 < 70), so "raise `failBelow` as the accepted-findings list shrinks" remains a real instruction.

## 5. What was changed

`.aislop/config.yml` (tool config, no source touched) and three source annotations:

- `ruff/UP031`, `ruff/FURB167`, `ruff/EXE001` set to `off`, each with the reason inline. These three
  accounted for 63 of the 84 default findings and conflict with deliberate project choices.
- `ci.failBelow: 45` — a **ratchet set from the measured baseline**, not a target. The comment in the
  config now also records the real exit-code rule, since the number alone implies it is the only gate.
- Three single-line `aislop-ignore-next-line ai-slop/swallowed-exception` directives at `:437`, `:914`,
  `:936`, each carrying its reason, so a reviewer sees the justification at the site and no future agent
  "repairs" a deliberate handler.

Still open, pending a decision:

1. **CI step, scoped to changes** — `npx --yes aislop@latest ci --changes` in `.github/workflows/`, gated
   on the config's `failBelow`. Now unblocked by (a); it is a policy decision (a required check on every
   PR) rather than a mechanical fix, so it is not enabled by default. Adds a Node dependency to CI
   (already present via the runner image).
2. **Optional pre-commit hook** — `aislop hook install` for per-edit scanning.

## 6. Telemetry and privacy

Anonymous aggregate only, and allowlist-filtered before send: version/OS/arch, an install UUID at
`~/.aislop/install_id` (0600), package-manager channel, `file_count_bucket`, score and finding *counts*.
**Not** collected: code, file paths, project/repo/branch names, prompts, raw diagnostics, secrets.
Opt-out precedence: `AISLOP_NO_TELEMETRY=1` / `DO_NOT_TRACK=1` → config `telemetry.enabled: false` → `CI=true`
(off by default in CI). `AISLOP_TELEMETRY_DEBUG=1 AISLOP_TELEMETRY_DRY_RUN=1` prints events without sending.
Acceptable for this project as configured; no opt-out set.

## 7. Limits worth remembering

- It cannot see the project's own documented rules; `scripts/check_agents_md.py` remains the authority for those.
- Severity and the score *label* are advisory, and the exit code is not the label. Read
  `findingAssessment.byKind` (`style-policy` / `confirmed-defect` / `ai-slop-indicator` /
  `conservative-security`) and triage from that.
- Every exit code is undocumented: read `dist/cli.js` rather than guessing from a run, since a single
  error-severity finding makes `failBelow` irrelevant.
- A suppression that does not match fails silently. After adding one, confirm the tool reports
  `Suppressed N finding(s)`; a clean-looking run proves nothing on its own.
- `aislop fix` is mechanical only; `--safe` restricts to behaviour-preserving edits. Neither replaces
  review, and neither should be run against `oh_brother.py` without checking the GPL header hash afterwards.

**Last reviewed:** 2026-09-13 (aislop 0.16.1, `oh-brother` @ `815aa51`, 137 tests passing)
