# Optimization & Portability Audit — Runtime, Autonomy, and Cross-Platform Behaviour

**Target:** `oh_brother.py` (1182 lines, 30 module constants, 27 functions) — GPLv2 fork of CauldronDevelopmentLLC/oh-brother
**Branch:** `harden` @ `9a2f7e5` (working tree clean; no source modified by this audit)
**Printer under test:** Brother HL-L2865DW @ 192.168.88.65, MAIN v1.24Q (vendor API also reports 1.24 — up to date)
**Audit date:** 2026-09-13
**Lens:** adversarial portability + performance, with the deployment target taken to be *unattended* (cron/systemd, no TTY)
**Question:** what fails on a platform or in an environment other than this one; what work is done that need not be; and which of those answers is actually worth a commit.

**Method:** full source read; stdlib source read for the platform claims (not assumed); four Phase-0
measurements against the real module (three of them end-to-end through `main()`); three parallel
discipline reviews (portability / architecture-efficiency / flash-safety-unattended) whose findings were
each re-verified against the source before being recorded here. Baseline: `128 passed` in 0.19s,
`scripts/check_agents_md.py` → No drift.

---

## 0. Bottom line

| # | Severity | Finding | Effort | Gate |
|---|---|---|---|---|
| P1 | HIGH | Non-ASCII stdout on 16 runtime sites launders a safety exit code: `EXIT_UPLOAD` (7) becomes `EXIT_ERROR` (1) and the DO-NOT-POWER-OFF warning is replaced by a traceback | S | — |
| P2 | HIGH | SIGTERM/SIGHUP have no handler, so the per-window interruption design is unreachable for the signals systemd/docker/timeout(1) actually send | M | G1 |
| P3 | HIGH | Worst-case in-flight window (~66 min) exceeds every common supervisor's kill grace (docker 10s, systemd 90s) → an un-interceptable SIGKILL can land mid-write | S (docs) | G4 |
| P4 | MEDIUM | Unconditional `os.replace` overwrites a verified retained image, and the fetch is unconditional too; integrity is length-only framing | M | G2 |
| P5 | MEDIUM | `.part` and the backup tree are CWD-relative with no lock and no early writability check → 15 MB spent, then exit 6 | S | — |
| P6 | MEDIUM | `sys.stdin`/`sys.stdout` can be `None`; the consent gate and every `flush()` then raise → exit 1 instead of a clean refusal (9) | S | G3 |
| P7 | LOW | R16 preflight still absent (known); `_query_printer_version` is the one SNMP call with no `wait_for`; dispatcher never closed | S | — |
| P8 | LOW | Hygiene: no `HTTP_TIMEOUT` constant; `MIN_FIRMWARE_SIZE` inlined inside a function; artifact regex implies a 3-digit version forever; duplicated fallback branches; unreachable `SystemExit` catch; R18 dead `ip` parameter | S | — |
| P9 | LOW | CI has no encoding test and no non-Linux leg, so P1's class and the README's "runs on any platform" claim are both unverified | S | — |

**Do not build:** the module-globals → context refactor, the 293-line `update_firmware()` decomposition,
the re-indent of that function, the `parser` relocation, and the persistent-dispatcher change. Reasons and
trigger conditions in §4.

**Single highest-value commit:** P1. It is the only finding where a *safety message* is lost and the
documented exit-code contract is silently violated, and it is a test-and-message change with no behaviour
risk. It is also the only one whose fix is verifiable without hardware.

---

## 1. Empirical evidence (reproduced in this audit)

| Probe | Result |
|---|---|
| Forced `UPLOAD_INCOMPLETE` branch of `update_firmware()` via `main()`, UTF-8 stdout | returns **7** (`EXIT_UPLOAD`), prints `FAILURE: firmware update did not complete (exit code 7)` |
| Same probe, `PYTHONIOENCODING=ascii` | returns **1** (`EXIT_ERROR`); `TRANSFER INCOMPLETE — DO NOT POWER OFF` replaced by `UnicodeEncodeError` raised at `oh_brother.py:900` |
| The three interruption/safety message builders under ASCII stdout | all three raise `UnicodeEncodeError` |
| Non-ASCII runtime sites, classified | 31 non-ASCII lines total; **16 are printed string literals** on stdout; 1 more is a `stderr` site (safe — see P1) |
| `sys.stderr.errors` | `backslashreplace` on every platform → `stderr` cannot raise `UnicodeEncodeError`. Only `stdout` is hazardous |
| Locale-stripped Linux (`env -i`, `LC_ALL=C`, `LC_ALL=POSIX`, `LANG=`, `PYTHONCOERCECLOCALE=0`) | **utf-8** in all 7 configurations (PEP 538/540). ASCII stdout needs an explicit override |
| Codec support for U+2014 | OK: utf-8, cp1252, cp1251, cp936, cp950. **FAIL: ascii, latin-1, iso8859-15, koi8-r, cp437, cp850, cp866, cp932** |
| Brother CDN `HEAD` on the artifact | `ETag: "c5306355c75a95fa6c04109c1d7f70b0:1777544884.181447"`, `Last-Modified`, `Content-Length` present. ETag prefix = the artifact's md5 |
| Brother CDN conditional `GET` (`If-Modified-Since`) | **304 Not Modified** — caching works |
| SnmpDispatcher churn: 10 sequential walks against the live printer | fds climb exactly +1 per walk (5→12) then collapse to 5 on GC; mean 0.13 s/walk → ≤8 s across a 60-poll window |
| SIGTERM during an in-flight upload (child process, real `update_firmware()` path) | returncode `-15`, **no** warning printed, image already retained at `firmware_backups/HL-L2865DW/1.24/` |
| `os.path.exists(` in the module | 0 occurrences — nothing ever checks whether a retained image already exists |
| Existing-file overwrite evidence | a `--test -c MAIN -f 1.23` run re-downloaded 15,270,855 bytes and `os.replace()`d it over a byte-identical file (md5 `c5306355c75a95fa6c04109c1d7f70b0`) |

---

## 2. Ranked findings

### P1 [HIGH] Non-ASCII stdout launders a safety exit code

`oh_brother.py:463, 475, 489, 604, 612, 620, 624, 730, 756, 759, 784, 791, 799, 861, 904, 1126` (16 stdout sites; `:1049` is `stderr` and therefore safe).

The em dash U+2014 is unencodable in `ascii`, `latin-1`/`iso8859-15`, `koi8-r`, `cp437`, `cp850`,
`cp866` and `cp932`. When `sys.stdout` uses one of those, `print()` raises `UnicodeEncodeError`, which
is a `ValueError` rather than a `KeyboardInterrupt`, so it is not caught by the per-window handlers at
`:894`, `:921`, `:1160` — it escapes to the top-level `except Exception` at `:1168`, which prints a
traceback and returns `EXIT_ERROR` (1).

**Failure:** the run reports "unexpected internal error" where the contract says "the printer may hold a
partial image", and the operator loses the DO-NOT-POWER-OFF instruction. The exit codes that can be
laundered into 1 by a *message formatting* bug are 4, 5, 6, 7 and 8.

**Scope, stated honestly (corrected — see §6):** modern Linux service environments do **not** trigger this.
Seven locale-stripped configurations all yield utf-8 via PEP 538/540 coercion; an ASCII stream requires an
explicit `PYTHONIOENCODING`/`PYTHONUTF8=0`. The reachable trigger set is: a Windows process whose stdout is
redirected with an OEM/legacy codepage (`cp437`/`cp850`/`cp866`) or a non-Western ANSI codepage (`cp932`),
a Windows non-console session (`pythonw`, a Scheduled Task not attached to a console — which per the `sys`
docs uses the system locale encoding even for the console device), any platform where an operator or
wrapper sets a non-UTF-8 `PYTHONIOENCODING`, and legacy single-byte Unix locales (`ISO8859-15`, `KOI8-R`).
Western Windows (`cp1252`) happens to be safe. Lower probability than the finding's shape suggests; the
consequence when it fires is severe, and the fix is one commit.

**Fix (S):** make the 16 stdout strings ASCII-only (`--`, `:`) and leave em dashes in comments and
docstrings, which are never printed. Then close the *class*, not the instance, at the boundary:

```python
# in main(), before any output
for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper):
        try: _stream.reconfigure(errors='backslashreplace')
        except (ValueError, OSError): pass
```

Use `backslashreplace`, *not* `replace`: `stderr` already defaults to `backslashreplace` and never raises,
so this makes the two streams consistent and preserves the information (`\u2014`) instead of degrading a
safety warning to `?` on exactly the platforms where it matters. The `isinstance` guard is required —
`sys.stdout` is not always a `TextIOWrapper` (pytest replaces it) and `reconfigure()` would raise
`AttributeError` there.

**Verification (RED first):** a test that runs the forced `UPLOAD_INCOMPLETE` path under an ASCII stdout
and asserts (a) no raise and (b) the return value is `EXIT_UPLOAD`, not `EXIT_ERROR`. Against today's
source it returns 1, so the test fails before the fix. Assert the `DO NOT POWER OFF`-class substring, not
the glyph — a test pinning the em dash would re-break on this very change (AGENTS.md's "assert the
mechanism, not the glyph").

---

### P2 [HIGH] SIGTERM/SIGHUP bypass the entire interruption design

`oh_brother.py:894, 921, 1160` (the only interruption machinery, `SIGINT`-only); `:17-35` (no `signal` import).

At import, `SIGTERM` and `SIGHUP` are `SIG_DFL`; `signal` is never imported. `systemd stop`,
`docker stop` and `timeout(1)` send **SIGTERM**. Reproduced live: a child driven through the real
`update_firmware()` path, SIGTERM'd while inside `sendfile()`, died with returncode `-15` — no
DO-NOT-TURN-OFF message, no retained-image notice, no structured exit code. The three carefully designed
outcomes (7 / 8 / 130) are unreachable for the signal that supervisors send.

**Good news, verified:** the retention invariant survives even an uninterceptable kill, because
`os.replace()` at `:816` completes *before* the socket opens at `:852`. A mid-upload SIGKILL cannot lose
the recovery copy. (It can still brick the printer — see P3 — but the image is on disk.)

**Fix (M):** install `SIGTERM`/`SIGHUP` handlers in `main()`, and **do not** translate them into
`KeyboardInterrupt`. A naive Ctrl-C-style abort at t=0 truncates a transfer that would have finished in
~2 s over a LAN, and wastes the supervisor's grace period that exists precisely to let it finish. Instead:
set a flag, print the window-appropriate warning to `stderr`, and return — letting the in-flight
`sendfile()` complete (PEP 475 retries the `EINTR` the signal delivered). The upload loop checks the flag
only at the top of each `while offset < fw_size` iteration, i.e. between `sendfile()` calls, never inside
one. If the transfer completes, skip verification and return `EXIT_UNVERIFIED` (8) — "uploaded,
unconfirmed" — which is the honest outcome. If it cannot continue, return `UPLOAD_INCOMPLETE` →
`EXIT_UPLOAD` (7). Outside the upload/verification windows, exit `EXIT_INTERRUPTED` (130). No new exit
codes: 7, 8 and 130 already mean what they need to mean. `SIGINT` must remain a real abort — this change
adds a signal, it must not soften Ctrl-C.

**Gate G1:** prove the flag design live before committing. Drive a real upload and send SIGTERM at both
boundaries — mid-`sendfile()` (assert the transfer still completes or returns 7/8 **with the warning
printed**) and between upload and verification (assert 8). Then confirm Ctrl-C still returns 7 mid-upload
and 130 elsewhere. The failure mode to rule out is the opposite of the bug: a flag checked in the wrong
place turning a completed transfer into a false failure. Use `timeout --preserve-status -s TERM` — the
project already learned that background jobs ignore SIGINT.

**Counter-argument:** the handler prevents no brick (the kill does that); it changes what the operator is
told. If that distinction is not valued, this is observability work rather than safety work. And if the
deployment never sends SIGTERM — a plain `nohup` run, or a nightly `oh-brother --yes` that normally exits
3 in ~10 s — the handler is dead weight. It becomes live work the moment the tool runs under a systemd
timer with a default `TimeoutStopSec`, which is the natural next step for this tool.

---

### P3 [HIGH] The in-flight window is longer than any supervisor's grace period

`oh_brother.py:98-99` (`UPLOAD_SOCKET_TIMEOUT=300`, `UPLOAD_STALL_DEADLINE=3600`), `:116-117`
(`FLASH_VERIFY_TIMEOUT=300`, `FLASH_VERIFY_POLL=5`), `:133` (`READY_TIMEOUT=300`, per category).

Worst case for one category is `3600 + 300` ≈ 65 min, plus 300 s per additional category. The supervisors
this tool is proposed to run under kill far sooner: `docker stop` SIGKILLs after 10 s by default, systemd
`TimeoutStopSec` defaults to 90 s. The exposure is not the nominal case (15 MB over a LAN takes ~2 s) —
it is the stuck case, which is exactly when a human or watchdog reaches for the kill. That SIGKILL lands
mid-write, uninterceptably.

**Fix (S, docs):** treat it as a deployment contract, since no code survives `SIGKILL`.
(1) State the requirement in the README's unattended section next to the exit-code table: the stop grace
(`systemd TimeoutStopSec=`, `docker stop -t`, cron `timeout`) must exceed `UPLOAD_STALL_DEADLINE +
FLASH_VERIFY_TIMEOUT` (~66 min), with literal unit snippets. (2) Emit one `stderr` line at the start of a
run stating the worst-case window. (3) **Gated on G4:** only after measuring the slowest real transfer over
the operator's link, consider lowering `UPLOAD_STALL_DEADLINE` — that narrows both the exposure and the
required grace. Do not move the constant without that measurement; the 300 s socket budget and 3600 s
deadline were raised deliberately after a real stall.

**Counter-argument:** if the operator never supervises or timeboxes the process, nothing here is reachable
and this is a paragraph of documentation about a scenario that never occurs. It is also fair to say a
mid-flash SIGKILL is not much worse than a mid-flash abort — in both cases the retained copy is on disk
and the printer holds a partial image — so "brick" overstates the delta. It stops being theoretical the
moment a systemd timer is added with default settings.

---

### P4 [MEDIUM] The fetch and the promotion are both unconditional; integrity is length-only

`oh_brother.py:746-748` (fetch whenever a URL is returned), `:750` (`.part` name), `:806-816` (integrity
check, then `os.replace`).

`os.path.exists(` appears nowhere in the module. The only check on incoming bytes is
`_verify_firmware_integrity` — declared-length equality plus a 100 KB floor — which is framing, not
integrity: a correct-length body with wrong content passes and *then* replaces a retained copy that had
already passed. This is the only code path that can damage `firmware_backups/`. It also re-fetches
15,270,855 bytes that were already on disk (measured).

**Fix (M), two independent parts:**

1. **Cheap vendor-authoritative reuse.** The CDN honours conditional requests, but **only via
   `If-Modified-Since`** — a correction to this audit's first draft, which credited the `304` below to the
   `ETag` and prescribed `If-None-Match` (see the reproducer in the appendix, which sent
   `If-Modified-Since` and got a real `304`). Measured against the live artifact: sending its own
   `ETag` back in `If-None-Match` returns `200` every time (exact value, lowercase header name, and the
   md5 prefix alone), while sending its own `Last-Modified` in `If-Modified-Since` returns `304`. The
   ETag is genuine — `"<md5 of the artifact>:<timestamp>"` — and simply ignored by that edge. The tool
   already calls the vendor API on every run, so record the validator at retention and send it back:
   `304` → reuse, print the decision; `200` → download as today. No new file format, no new assumption,
   and it fails open. Prefer `Last-Modified` over the stronger-looking `ETag` on that measurement, not
   on taste: a validator the server ignores costs a full 15 MB download on every run.
2. **Protect the retained copy.** Record a `sha256` sidecar at retention time; before overwriting, compare.
   A missing or mismatched sidecar means download, never trust.

**Gate G2:** the reuse test must be a *matching recorded hash*, never size-only or exists-only — that
would be a gate weakening, making a truncated retained image the trusted input with no vendor
corroboration, strictly worse than today. Prove RED first: an existing file with a wrong recorded hash
must trigger a re-download.

**Counter-argument:** keeping an unconditional fetch is defensible — the bytes are the ones the vendor
just offered, and reuse risks keeping a stale build if Brother republishes under an unchanged filename.
Mitigation: a republish under the same name leaves the version string unchanged, so the vendor API would
not differentiate them either — and the retained image is the one that already passed a check the new
bytes have not. Part 2 (the sidecar) is the safety-relevant half; part 1 is bandwidth.

---

### P5 [MEDIUM] CWD-relative download, no lock, no early writability check

`oh_brother.py:120` (`BACKUP_DIRNAME`), `:445-448` (`_firmware_backup_path`), `:750` (`.part` in CWD),
`:813-820` (`os.makedirs` + `os.replace` + the `OSError`→`EXIT_DOWNLOAD` handler).

Downloaded bytes land as `<name>.part` in the current working directory; the image is promoted to
`firmware_backups/<MODEL>/<version>/` relative to the same CWD. Under `systemd` with
`ProtectSystem=strict`/no `WorkingDirectory`, a container with `cwd=/`, or a Windows Scheduled Task
defaulting to `C:\Windows\System32`, the write fails — after the full ~15 MB transfer — with `EXIT_DOWNLOAD`
(6). Two concurrent runs in one CWD collide on the same `.part` name; there is no lockfile, and no
`tempfile`/`mkstemp` anywhere.

**Fix (S):** unique `.part` name (`mkstemp` or pid), a backup root resolved from flag/env/XDG with a CWD
fallback, and a one-shot writability attempt *before* the download (after the consent gates, ~`:744`),
returning `EXIT_DOWNLOAD` with a clear message. An env var, not a CLI flag, keeps the frozen surface
intact. Retention invariants unchanged.

**Counter-argument:** the existing behaviour fails *safely* (nothing written, `.part` removed) — the
defect is wasted bandwidth and an opaque code, not data loss, and README/AGENTS.md both document CWD
retention as intentional. If the maintainer holds that line, the minimal defensible change is the early
writability check alone, dropping the override.

---

### P6 [MEDIUM] `sys.stdin` / `sys.stdout` can be `None`

`oh_brother.py:265` (`prompt`), `:740` (the consent gate), `:675, 748, 795, 843, 1028, 1120` (`stdout` use/flush).

CPython sets `sys.stdin`/`sys.stdout` to `None` when the underlying fd is closed, and `pythonw.exe` /
GUI-embedded interpreters get `NULL` standard handles. `sys.stdin.isatty()` and `sys.stdout.flush()` then
raise `AttributeError` (`print()` to a `None` stdout is a silent no-op, but `flush()` is not). A
console-less launch dies at the first flush in the SNMP stage and is swallowed by the top-level handler →
`EXIT_ERROR` (1) where the intended outcome is a clean `EXIT_REFUSED` (9) without `--yes`.

**Fix (S):** `def _isatty(s): return bool(s) and s.isatty()` used by the gate and `prompt()`, plus a
`_flush()` guard. Missing stream ⇒ treated as "not a terminal", which preserves the default-deny posture.

**Gate G3:** confirm a real Windows Scheduled Task / `pythonw` launch, or accept it as harmless defensive
code — on Linux, `systemd` supplies a valid `/dev/null` (`isatty()` False, no exception) and cron a valid
fd, so the `None` case may be rarer than the issue title suggests.

---

### P7 [LOW] Bounded-walk gap, dispatcher lifetime, and R16

- **Inconsistent bounding (real):** `_query_printer_version` (`:502`) is the only SNMP call
  *without* `wait_for` — `_printer_ready` (`:552`) and `main()` (`:1031`) both use it. This call is on the
  flash-verification path (`:534-542`), so one slow walk can blow a poll's budget. Fix (S): wrap it
  identically. 21 tests patch `_snmp_walk_table` wholesale, so the seam is unaffected.
- **Dispatcher lifetime (measured, downgraded):** `SnmpDispatcher()` is constructed per walk (`:968`) and
  never closed. Measured against the live printer: fds climb exactly +1 per walk then collapse on GC —
  deferred release, not a leak, peak 12 fds. A 60-poll window wastes ~8 s of loop setup. **Verdict: not
  worth fixing.** A `try/finally: dispatcher.close()` is a legitimate 2-line tidy (no seam impact) but
  buying a persistent dispatcher would break the `_snmp_walk_table` test seam for single-digit seconds.
  Note: never *share* a dispatcher across `asyncio.run` calls — it owns transports bound to one loop.
- **R16 preflight (already listed as unfixed):** gates are checked before the download, but nothing
  verifies the printer will accept an upload, so the 15 MB is spent before a TCP/FTP refusal surfaces.
  Fix (S): TCP connect to 9100 before the fetch, scoped to `not args.test` (a raw-port-disabled printer
  should still yield a backup on `--test`). Read-only by construction; must **not** be described as an
  upload safety gate — a successful connect proves the port is open, not that the image will be accepted.
  With P4's conditional GET, the redundant fetch largely disappears anyway.

---

### P8 [LOW] Hygiene — mostly the repo grading its own homework

| Item | Location | Note |
|---|---|---|
| No `HTTP_TIMEOUT` constant | `:583`, `:592`, `:754` | `timeout=30` inlined three times; AGENTS.md says every timeout is a named constant |
| `MIN_FIRMWARE_SIZE = 102400` inlined in a function | `:407` | **Trap:** the drift checker asserts `**Module constants (N):**` against the real count, and the checker's own regex only counts column-0 names. Hoisting this makes it 30 → 31 and `check_agents_md.py` **fails** until AGENTS.md is updated in the same commit |
| Artifact regex assumes a fixed 3-digit version | `:317` (`_(\\d{3})([A-Za-z])`) | Verified `None` for 2- and 4-digit forms (`X_12Q`, `Y_1245Q`, `V_1Q`), which lands in the warn-and-proceed branch — the unsafe direction for a downgrade gate |
| Duplicated fallback branches | `:692-696` ≡ `:700-704` | Byte-identical; collapses to one decision point with both prints and the `EXIT_CURRENT` return preserved. Guard test: `test_vcheck1_does_not_upload_without_reflash` must still assert `_try_version_fallback` is **not** called |
| Unreachable `SystemExit` catch | `:503` | `_snmp_walk_table` raises `SnmpError`; nothing in the chain raises `SystemExit` |
| R18 dead `ip` parameter | `:361-398`, call site `:869` | Never read. Not cosmetic in one sense: a function that decides whether a flash succeeded should not advertise an address it ignores |
| `_version()` catches only `PackageNotFoundError` | `:222-226` | A malformed-metadata `ImportError` would make the module un-importable. Widen the `except` — the only part of the import-time finding worth doing |
| SSL guidance recommends `pip install certifi` alone | `:610-617` | `urllib` consults OpenSSL's default paths, not `certifi`, so that alone does not repoint trust. Lead with the platform remedy (`Install Certificates.command` on macOS, `ca_root_nss` on FreeBSD) and mention `SSL_CERT_FILE`. Same ASCII treatment as P1 |

---

### P9 [LOW] CI cannot see any of this

`.github/workflows/ci.yml` runs `ubuntu-latest` only, matrix 3.10/3.13, and every step is ASCII-clean.
The entire P1 class reproduces in seconds on the existing runner with an env var, so the high-value
addition is an **encoding test**, not a new platform leg: one pytest case that either asserts each
operator-facing constant `.encode('ascii')` succeeds or spawns a message-producing path with
`PYTHONIOENCODING=cp932`. Keep the `pysnmp.hlapi.v1arch` import pin — it is what turns a pysnmp 8.x rename
into a red build.

Optional: a single `windows-latest` smoke job (`--version`, `--help`, tests) plus 3.12 to close the middle
of the declared support band. Counter-argument, which is why it is optional: Windows runners bill at 2×
and macOS at 10×, and because `conftest.py` mocks pysnmp away, a platform leg mostly re-runs the same
pure-function tests under a different `os.path`. The honest minimum is the encoding test.

---

## 3. Phases with decision gates

**Phase 0 — verification (done, no code).** Settled the encoding defect end-to-end; proved the CDN supports
conditional GET; measured the dispatcher churn (negative result); read the stdlib rather than assuming
(`SOL_TCP` is portable — CPython defines it as 6 when absent; `socket.sendfile` works on Windows via a
pure-Python `send()` fallback and returns an int, so there is **no** Windows upload defect).

**Phase 1 — P1. Ships alone, no hardware needed.** ASCII-only stdout strings + the `backslashreplace`
boundary guard + the RED-then-GREEN encoding test. Highest value / lowest risk commit in this audit.

**Phase 2 — P2, gated on G1.** SIGTERM/SIGHUP as a between-calls flag, never as an abort; live proof of
both boundaries plus unchanged Ctrl-C behaviour.

**Phase 3 — P4, P5, P6, P7.** Ops hardening, each independently shippable. G2 gates the reuse path; G3
gates nothing code-wise (the guard is defensive either way).

**Phase 4 — P3 docs, then P8.** P3's documentation lands with Phase 3 (it describes the deployed
contract). P8 is a pure refactor batch, last so it can be dropped without loss: two constants, one regex,
one collapse, two deleted bits of dead code, and the SSL wording. Run `scripts/check_agents_md.py` after —
and update AGENTS.md's constant count in the same commit as the `MIN_FIRMWARE_SIZE` hoist.

---

## 4. Explicitly NOT doing (with the condition that would change it)

| Item | Why not | Trigger to revisit |
|---|---|---|
| `FlashContext` dataclass replacing the 5 mutable module globals | AGENTS.md declares "procedural style, module-level globals" a deliberate choice. Cost is 45–55 test call sites (66 direct global writes, 28 `update_firmware(` sites); benefit is zero real-world bugs, since the CLI runs once and the category loop is sequential. Shipping it would also falsify a documented decision, which the drift checker cannot catch | A second consumer (library import, a second printer, a parallel category loop) or a genuinely concurrent use |
| Decomposing the 293-line `update_firmware()` | Every early return in it *is* the frozen exit-code contract; the single linear body is why the operator-facing story is inspectable. AGENTS.md's Pipeline table documents its responsibility. The 45+ end-to-end tests are the real safety net, and they already exist without the split | A 6th gate, or a second upload transport — then the seams stop paying for themselves |
| Re-indenting `update_firmware` from upstream 2-space style | A 293-line whitespace diff attributes the whole safety-critical function to the reformatter and destroys `git blame`, and the project has already, deliberately, carried this block through seven hardening phases (AGENTS.md's changelog scopes "indentation" at Phase 6 and this block survives) | Never for style alone; only as a side effect of the decomposition above |
| Relocating the module-level `parser` | 19 `TestCLI` tests — including one whose entire purpose is asserting `oh.parser` exists — depend on it. Gain: one sub-millisecond metadata read. The `_version()` `except` widening is the only part worth doing | A second entry point |
| A persistent SNMP dispatcher / single long-lived event loop | Measured: ~8 s across a 60-poll window, fds released by GC. Breaks a 21-site test seam | A measured leak (unbounded fd growth) or a poll count an order of magnitude higher |

---

## 5. Open hypotheses (not findings — each with the command that settles it)

| Hypothesis | Settling command |
|---|---|
| A signal handler that returns (rather than raising) while `socket.sendfile` blocks actually lets the transfer finish, per PEP 475 `EINTR` retry | Phase 2 / G1: `timeout --preserve-status -s TERM` against a real upload and compare bytes sent before/after |
| `pysnmp`'s `UdpTransportTarget` accepts an IPv6 literal (`2001:db8::1`) | `python3 -c` one-liner constructing the transport with a v6 literal; currently untested, and the CLI has no `[addr]:port` form |
| An unclosed `SnmpDispatcher` leaks on the *Windows* Proactor loop as it does transiently here | Repeat the fd probe on a Windows host; on Linux it is GC-bounded |
| A Windows Scheduled Task hands the child a valid `NUL` handle rather than `None` (which would make P6 theoretical) | Run P6's probe under a real Task Scheduler entry |

---

## 6. Calibration — what this audit got wrong first time

Recorded because a stated confidence that is never checked stops tracking accuracy.

1. **"The em-dash bug breaks any cron/systemd run" — overstated.** Seven locale-stripped Linux
   configurations all give utf-8 (PEP 538/540); ASCII needs an explicit override. The trigger set is
   Windows OEM/non-Western codepages, non-console Windows sessions, explicit `PYTHONIOENCODING`, and
   legacy Unix locales. Same fix, narrower urgency.
2. **"3 non-ASCII message sites" — undercounted.** 16 printed stdout sites, not 3.
3. **"Fix it at the boundary with `errors='replace'`" — partly wrong.** `replace` would render a safety
   warning as `?` on exactly the affected platforms, and `reconfigure()` raises `AttributeError` when
   `stdout` is not a `TextIOWrapper`. `backslashreplace` plus an `isinstance` guard is the correct form —
   and it matches what `stderr` already does.
4. **"Dispatcher churn may leak — fix it" — downgraded by measurement.** +1 fd per walk, released by GC.
   Not worth a commit; a persistent dispatcher would cost a 21-site test seam for ~8 s.
5. **"Windows `socket.sendfile` is a portability risk" — refuted.** It falls back to a blocking `send()`
   loop and returns an int; the tool's 300 s socket timeout keeps it off the `ValueError` path.
6. **My first two probes were wrong and were discarded, not reported.** One called the message builders
   without printing their return values (a false "prints fine"); one replaced `socket.socket`, which broke
   asyncio's event-loop self-pipe so `main()` never reached the branch under test — it returned 1 for the
   wrong reason. Both were caught by asking *why* the result was what it was, and the corrected probe
   reproduces the defect.

---

## Appendix — reproducers

Both harnesses live in `/tmp` and were run read-only against the module; neither modifies the repo, and no
printer traffic is generated (the upload path is stubbed and `getaddrinfo` is redirected to a throwaway
local listener — never patch `socket.socket` directly, it breaks asyncio's self-pipe).

```bash
# P1: 16 stdout sites raise under a non-UTF-8 stdout
cd ~/Documents/Projects/oh-brother
PYTHONIOENCODING=ascii python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('oh', 'oh_brother.py')
oh = importlib.util.module_from_spec(spec); spec.loader.exec_module(oh)
for fn, args in (('_incomplete_message', ('p.djf',)),
                 ('_interrupt_during_upload', ('p.djf',)),
                 ('_interrupt_during_verification', ('p.djf', '1.24'))):
    try: print(oh.__dict__[fn](*args))
    except Exception as e: print(fn, 'RAISED', type(e).__name__)"

# P1 end-to-end: exit 7 vs exit 1 on the same code path
python3 /tmp/probe_exit_code.py                     # -> main-returned-7
PYTHONIOENCODING=ascii python3 /tmp/probe_exit_code.py  # -> main-returned-1

# P4: the CDN answers conditional requests
curl -sI http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf | grep -iE 'etag|last-modified|content-length'
curl -s -o /dev/null -w '%{http_code}\n' -H "If-Modified-Since: $(date -u -R)" \
     http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf     # -> 304

# P4 correction: which validator does it actually act on? Send each one back.
ETAG=$(curl -sI http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf \
       | sed -n 's/^[Ee][Tt][Aa][Gg]: *//p' | tr -d '\r')
LM=$(curl -sI http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf \
     | sed -n 's/^[Ll]ast-[Mm]odified: *//p' | tr -d '\r')
curl -s -o /dev/null -w 'If-None-Match  -> %{http_code}\n' -H "If-None-Match: $ETAG" \
     http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf     # -> 200 (ignored)
curl -s -o /dev/null -w 'If-Modified-Since -> %{http_code}\n' -H "If-Modified-Since: $LM" \
     http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf     # -> 304 (honoured)
```

**Last reviewed:** 2026-09-13 (branch `harden`, 159 tests passing and no drift as of Packet 3B; §P4's
conditional-request prescription was corrected after the live run — the ETag is not honoured, see the appendix)
