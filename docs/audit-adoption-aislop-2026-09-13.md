# aislop — adoption evaluation

**Tool:** [scanaislop/aislop](https://github.com/scanaislop/aislop) v0.16.1 (MIT, Node ≥ 20)
**Evaluated:** 2026-09-13 against `oh-brother` @ `9a2f7e5`
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
inline suppression (`# aislop-ignore-next-line <rule> -- reason`), `.aislop/config.yml`, GitHub Action,
SARIF output, and `aislop agent` (drives a local coding agent in a worktree). MIT, free CLI.

## 2. Measured on this repo (not the README's claims)

Read-only — no repo files were created or modified by a scan.

| Run | Score | Findings | Composition |
|---|---|---|---|
| Default rules | **25** ("Critical") | 84 | 56 = one rule (`ruff/UP031`, `%`-formatting) vs this project's deliberate house style; 7 = `ruff/FURB167` (`re.M` → `re.MULTILINE`), classified as **errors**; remainder small |
| With UP031 / FURB167 / EXE001 off | **47** | 20 | 16 style-policy, 4 ai-slop-indicator, 0 confirmed defects, 0 security |

Performance: 86–110 ms for 3 files. `security`: 0 findings.

### The AI-slop engine's 4 hits, checked by hand

| Hit | Verdict |
|---|---|
| `ai-slop/swallowed-exception` `oh_brother.py:431` (`_remove_quietly`: `except OSError: pass`) | **False positive** — the docstring says "Best-effort file removal that never masks the original error". Deliberate |
| `ai-slop/swallowed-exception` `:870`, `:891` (upload OSError handlers that print and continue) | Deliberate-by-design: the handlers print, and the post-upload version verification is authoritative (R9 in AGENTS.md). Not silently swallowed |
| `ai-slop/python-repetitive-dispatch` `:158` (5 sequential `if name == …` on SNMP key/value rows) | **False positive** — rewriting as a dispatch table would be less readable |
| `ai-slop/meta-comment` `:1001` | Cosmetic; a comment on an exit path |

**Zero true positives on existing code.** The one genuinely useful signal in the default run was convergence with
this session's audit rather than anything new:

- `code-quality/complexity/function-too-long` at `:651` — the 293-line `update_firmware()` (audit §4, deliberately not split).
- `code-quality/complexity/function-too-long` at `:995`, `:57`; `file-too-large`.
- `lint/ruff/BLE001` (blind `except`) at `:503`, `:555`, `:1168` and `ruff/PLW0602` (`global` declared, never assigned) at `:652` — the same class as audit findings P8 (`_query_printer_version` catching `(Exception, SystemExit)`) and the module-globals decision.
- `lint/ruff/PIE810` at `:356`, `I001` at `:17`, `PLW1510` in the checker — real but trivial.

It found **nothing** about the em-dash/exit-code laundering, SIGTERM/SIGHUP, the 66-minute in-flight window,
the unconditional `os.replace`, the CWD-relative download, or the inlined `timeout=30` / `MIN_FIRMWARE_SIZE`.
It also cannot know this project's own rules (the named-constant convention, the frozen exit-code contract).

## 3. Verdict

**Adopt it as a guard on incoming agent-authored code, not as a detector of past slop and not as a raw score gate.**

- Against hand-hardened code it is weak: 0 true positives across 84 findings, and its headline severity labels are
  unreliable (7 `re.M` readability preferences reported as *errors*; a documented `except OSError: pass` reported as a
  swallowed error). A repo with 128 tests, a drift checker and a byte-identical licence header scores "Critical" at 25
  mostly for using `%`-formatting.
- As a **net for code an agent wrote this session** it is genuinely well suited: deterministic, ~90 ms, no LLM, no
  network, read-only, `--changes`/`--staged` scoping, and an inline suppression mechanism with a required reason —
  which is exactly the shape needed to catch narrative comments, invented fallbacks and duplicated helpers *before*
  a diff is reviewed. The Crush handoff is the first real test of that role.

## 4. What was changed

`.aislop/config.yml` (new, read-only tool config — no source touched):

- `ruff/UP031`, `ruff/FURB167`, `ruff/EXE001` set to `off`, each with the reason inline. These three account for
  63 of the 84 default findings and conflict with deliberate project choices.
- `ci.failBelow: 45` — a **ratchet set from the measured baseline (47)**, not a target. It fails any new finding
  without demanding a churn diff on a GPLv2 fork of upstream code. Raise it as the accepted-findings list shrinks.

Not yet done, pending a decision:

1. **CI step, scoped to changes** — `npx --yes aislop@latest ci --changes` in `.github/workflows/ci.yml`, gated on
   the config's `failBelow`. Whole-repo scoring of a fork would force churn on upstream-derived style; per-change
   scoring gates only new code. Adds a Node dependency to CI (already present via the runner image).
2. **The 3 false positives** — the designed mechanism is `# aislop-ignore-next-line ai-slop/swallowed-exception -- <reason>`
   at `:431`, `:870`, `:891`. Left unannotated for now because that means editing source in a licensed fork; do it
   when the CI gate is switched on, so the justification lands where a reviewer will see it.
3. **Optional pre-commit hook** — `aislop hook install` for per-edit scanning.

## 5. Telemetry and privacy

Anonymous aggregate only, and allowlist-filtered before send: version/OS/arch, an install UUID at
`~/.aislop/install_id` (0600), package-manager channel, `file_count_bucket`, score and finding *counts*.
**Not** collected: code, file paths, project/repo/branch names, prompts, raw diagnostics, secrets.
Opt-out precedence: `AISLOP_NO_TELEMETRY=1` / `DO_NOT_TRACK=1` → config `telemetry.enabled: false` → `CI=true`
(off by default in CI). `AISLOP_TELEMETRY_DEBUG=1 AISLOP_TELEMETRY_DRY_RUN=1` prints events without sending.
Acceptable for this project as configured; no opt-out set.

## 6. Limits worth remembering

- It cannot see the project's own documented rules; `scripts/check_agents_md.py` remains the authority for those.
- Severity and the score *label* are advisory. Read `findingAssessment.byKind` (`style-policy` / `confirmed-defect` /
  `ai-slop-indicator` / `conservative-security`) and triage from that, not from the 0–100 number.
- `aislop fix` is mechanical only; `--safe` restricts to behaviour-preserving edits. Neither replaces review, and
  neither should be run against `oh_brother.py` without checking the GPL header hash afterwards.

**Last reviewed:** 2026-09-13 (aislop 0.16.1, `oh-brother` @ `9a2f7e5`, 128 tests passing)
