# Production Hardening Plan — oh-brother

**Date:** 2026-09-13
**Branch:** `harden`
**Inputs:** `docs/audit-safety-write-path-2026-09-13.md` (safety lens) + production-engineering
audit (packaging/lifecycle lens). Both reached **PROCEED** (88% / 93%).
**Baseline gate:** `74 passed, 1 skipped`; pysnmp 7.1.27; Python 3.13.14; live `--test` against
HL-L2865DW @ 192.168.88.65 confirmed R1 and R4.

---

## 0. Framing decision (host Skeptic, overrides both experts)

Both reviewers optimised for *"make this safe to run unattended."* The higher-leverage reading
is: **do not run firmware flashing unattended at all.** This is a home printer 3 m away whose
firmware changes twice a year. The deliverable is therefore not flashing automation — it is
**making the dangerous path impossible by default**, so that a stranger who *does* cron it
cannot brick their printer. Consequences, applied throughout:

- Default-deny (R1) outranks verification polish (R5) in priority.
- `--allow-downgrade` is **cut**. One opt-in flag (`--reflash`), version-equality only.
- Post-upload verification must never manufacture a false `FAILED` (§Phase 3, R5).

## 1. Expert disagreement resolved

| Fact | Expert A | Expert B | Ruling |
|---|---|---|---|
| `prompt()` no-ops when stdin is not a TTY (`oh-brother.py:183-186`) | **CRITICAL** — flashes with zero confirmation | "cron safety already holds" | **A.** Under the stated threat model (unattended, no operator) the no-op is the defect, not the feature. It is what compounds R1 into a repeating loop. |

## 2. Exit-code contract (frozen before automation can depend on it)

Adopt Expert B's table, plus `UNVERIFIED` and `REFUSED`. Ship it in `--help` and README in the
same commit as the tests that assert it. Aggregation is **worst-wins** so a multi-category run
cannot hide an upload failure behind a success.

```
0  OK          firmware uploaded and verified; or --test fetched+verified a blob
1  ERROR       unexpected internal error (uncaught exception)
2  USAGE       bad arguments (argparse default)
3  CURRENT     nothing to do — printer already current          [cron: healthy no-op]
4  PRINTER     printer unreachable (SNMP 161 / TCP 9100 / FTP 21) [retryable]
5  VENDOR      Brother API error, or no firmware URL after fallback [retryable]
6  DOWNLOAD    download or integrity check failed                [retryable]
7  UPLOAD      upload failed / printer rejected the image        [needs a human]
8  UNVERIFIED  uploaded, but the post-flash version check was inconclusive
9  REFUSED     a safety gate declined to proceed (non-TTY without --yes;
               downgrade blocked; missing model/spec)
```

`REFUSED` is deliberately distinct from `USAGE`: it means "the tool declined for safety
reasons and a human should look", not "you typed the arguments wrong".

## 3. Phases

Each phase is independently revertible and must end green on `npm`-equivalent gate:
`python3 -m pytest tests/ -q`.

### Phase 1 — Tests first (RED), no implementation
Write the failing tests for R1–R8 before touching the source, then prove they are not vacuous:
`git stash` the source, run the new tests against the **original** `oh-brother.py`, and confirm
they FAIL. A test that passes against the unfixed source is worthless and must be rewritten.
**Must also rewrite** (not merely accompany) `tests/test_oh_brother.py:563
test_vcheck1_fallback_succeeds` — it currently *pins R1 as intended behaviour*.

### Phase 2 — Safety control flow (R1, R2, R4, R6, R7, R8, exit codes)
One coherent change to `update_firmware`'s control flow; these are interdependent because every
return site must emit a code.
- R2: the upload gate becomes `args.yes or (isatty and confirm)`. No TTY and no `--yes` →
  `REFUSED` before any socket is opened.
- R1: `version_check == '1'` returns `CURRENT` **before** download unless `--reflash` is set.
  The fallback URL never authorises a write on its own. With `--reflash`, the parsed artifact
  version must **equal** the installed version (a true reflash). Strictly-older → `REFUSED`.
- R7: abort before the vendor query when `model` or `spec` is falsy (no literal `"None"` in
  the XML). Add the `_(\d{3})([A-Za-z])` filename→version parse (`D02FZM_124Q` → `1.24`).
  Unparseable platform names (LZ*/D00* letter-only) are treated as **not** safe to auto-flash.
- R8: `host = parsed.hostname; ok = host == d or host.endswith('.' + d)` — the bare
  `netloc.endswith(d)` suffix check lets `evilbrother.com` and `notbrother.com` pass.
- R6: tri-state return driven by the code table; `main()` rewritten so a failure stops
  printing "No firmware update was needed"; `def main() -> int` + `sys.exit(main())`.
- R4: retain the image. Write to `<name>.part`, `os.replace` after integrity passes, keep under
  `firmware_backups/<MODEL>/<version>/`; **`--test` never deletes**; delete only after a
  verified success.

### Phase 3 — Transfer integrity (R3, R5)
- R3: raise the per-operation budget (60 s → 300 s) and classify "timed out/errored with
  `offset > 0` bytes already sent" as `INCOMPLETE` — do **not** delete, print an explicit
  `TRANSFER INCOMPLETE — DO NOT POWER OFF; reflash from <path>`, exit non-zero. The current
  `settimeout(60)` bounds one whole `sendfile()` call, so a throttling printer loses a
  mid-flight flash and the recovery image with it.
- R5: after upload, poll SNMP `FIRMVER` (readiness-poll first, then compare) against the
  expected version. **`UNVERIFIED` ≠ `FAILED`**: the printer reboots for 60–120 s after a
  flash, so "did not come back in time" must not be reported as failure — that path causes
  people to reflash a working printer. Report `expected=… actual=…`.

### Phase 4 — Packaging & CI (mechanical, no behaviour change)
- `git mv oh-brother.py oh_brother.py` (hyphens are illegal in entry-point module paths).
  Keep the GPL header byte-identical (attribution constraint). Update the test loader, README,
  AGENTS.md.
- Add `pyproject.toml`: `[project.scripts] oh-brother = "oh_brother:main"`,
  `dependencies = ["pysnmp>=7.1.0,<8"]`, `license = "GPL-2.0-only"`, upstream in `authors` +
  `[project.urls] Upstream`, so attribution survives packaging.
- `--version` read via `importlib.metadata`, never a duplicated literal (drift-proof).
- **Rewrite the README install section.** `README.md:26,32` currently instruct
  `apt-get install python3-pysnmp4` — pysnmp **4.x**, which lacks
  `pysnmp.hlapi.v1arch.walk_cmd`. The documented Debian path is an immediate `ImportError`.
  pipx becomes the primary install; the curl-a-single-file era ends (a shim cannot work: a
  curl'd script cannot import a sibling module it does not have).
- CI must **install the package**, not just run pytest: `tests/conftest.py` substitutes a
  `MagicMock` for pysnmp at `sys.modules` level, and the suite reports `74 passed` in a venv
  with no pysnmp installed. CI must run
  `python -c "from pysnmp.hlapi.v1arch import walk_cmd"` so an 8.x rename fails in CI rather
  than at a stranger's printer at 03:00. (Pairs with the `<8` cap.)
- Un-skip `test_test_flag_stops_before_upload`. Root cause is **not** urlopen interception: the
  mock's `read.return_value` never returns `b''`, so the unbounded download loop at `:420-425`
  spins (measured: 200,001 `read()` calls). Fix the mock, and sever the API leg at the
  `_http_post` seam so `urlopen` is unambiguously the download.

### Phase 5 — Follow-ups — CLOSED

R9 FTP timeout · R10 bounded download + `.part` atomicity · R11 readiness poll instead of
`sleep(30)` · R12 `-f` requires `-c` · R13 `--beta` needs `--yes` · R14 `_decrement_version`
zero-padding · R15 `getaddrinfo` inside `try` · R17 traceback on `--verbose` · R19 signal
handling. Also deferred: full `logging` refactor (60 call sites — exit codes already carry the
cron signal), full type hints (except the load-bearing `main() -> int`), `--log-file`,
`--dry-run` as distinct from `--test` (rejected — `--test` already is one). Add `--json` only
when a second consumer actually exists.

**Landed** in `7687a5d` and `480633c`. The deliberately rejected/deferred items above stay
closed by decision, not by neglect. Still open from the audit: R16 (no reachability preflight
before the ~15 MB download) and R18 (unused `ip` parameter in `_tcp_upload`).

One correction to the audit worth recording. R12 was rated MEDIUM, but `-c` on its own sent the
`B0000000000` sentinel as the installed version — and that is exactly the value the downgrade
check compares the artifact against. `_version_tuple('B0000000000')` is `None`, so forcing a
category silently turned the R7 downgrade *refusal* into a warning, in the one code path a user
enters specifically to force an unusual flash. It is now looked up from the SNMP map, with a
refusal when the printer does not report that category at all.

Verification note for anyone reading this later: R12, R13, R14 and the honest download path were
verified live against HL-L2865DW @ 192.168.88.65 using read-only commands only. R9's FTP
teardown, R10's abort path and R11's readiness poll are unit-tested only — exercising them live
would need a real flash (R9, R11) or a vendor CDN that lies about `Content-Length` (R10).

## 4. Decision gates

| Gate | Condition | Action if failed |
|---|---|---|
| G0 baseline | `74 passed, 1 skipped` recorded | stop |
| G1 tests RED | every new test fails against unfixed source | rewrite the test, do not proceed |
| G2 phase 2 | suite green + live `--test` leaves the artefact + non-TTY run exits `9` | stop, fix control flow |
| G3 phase 3 | live flash on 192.168.88.65 reports `expected == actual` and exits `0` | stop, treat as `UNVERIFIED` bug |
| G4 phase 4 | `pipx install git+<repo>` works; CI green; `oh-brother --version` prints | stop |
| G5 final | `git diff` reviewed, AGENTS.md + README updated, upstream attribution intact | stop |

## 5. What this work must NOT do
No new runtime dependency (pysnmp stays the only one). No module tree, no `Context` dataclass,
no config file. Keep the move of the module-level `parser` out of scope — `test_parser_accessible`
depends on it. Do not silently "fix" the deliberately-misspelled `SELIALNO` tag or the
`SPECIALITY`-style vendor quirks. Budget: **~130–200 net lines**; anything larger is a rewrite
and must be justified against brick-risk reduction.

## 6. Counter-argument (R25 — how this plan is wrong)
The plan assumes Brother's filename convention (`_(\\d{3})` → version) is stable. If a CDN
rename moves or drops those digits, "unparseable ⇒ do not auto-flash" makes the tool refuse
*legitimate* updates while exiting like a healthy no-op — a printer quietly stays on a
vulnerable firmware. Mitigation: refuse only on a **parseable and strictly older** version;
when unparseable, warn loudly and print the raw filename + version instead of silently
declaring "already current". Second failure mode: `--reflash` gets written into a systemd unit
during a debugging session and never removed. Mitigation: the `--yes`/TTY gate (R2) still
stands independently of `--reflash`.
