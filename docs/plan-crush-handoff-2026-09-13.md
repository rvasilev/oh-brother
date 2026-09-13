# Crush handoff plan — implementing the optimization/portability audit

**Source of truth:** `docs/audit-optimization-portability-2026-09-13.md`
**Quality net:** `docs/audit-adoption-aislop-2026-09-13.md` + `.aislop/config.yml`
**Repo:** `~/Documents/Projects/oh-brother`, branch `harden` @ `c8c85ed` (treat the branch as disposable — work stays local until reviewed)
**Instruction to the agent:** this is a GPLv2 fork. Preserve behaviour, preserve the licence header, and make no change that is not in the packet you were given.

---

## Status

| Packet | State | Evidence (independently verified 2026-09-13) |
|---|---|---|
| 1 — P1 non-ASCII / exit-code laundering | **Done, committed `4b4c054`** | 130 tests pass in 0.19s; drift checker `No drift`; GPL header md5 unchanged (`37f53a…eb07`); RED reproduced independently in a throwaway copy with the original source restored — `assert 1 == 7` with the upload window genuinely reached; live smoke test against HL-L2865DW clean (0 non-ASCII bytes, no traceback, image retained at md5 `c5306355c75a95fa6c04109c1d7f70b0`), and an `LC_ALL=C PYTHONIOENCODING=ascii` run returns 3 with no raise. Files: `oh_brother.py`, `tests/test_oh_brother.py`, `AGENTS.md`. |
| 2 — P5 download safety | **Done, committed `815aa51`** | Delivered by Crush, then independently verified: the packet's 5 tests re-derived RED against the pre-packet-2 source (4 failed, 1 passed — stale partial consumed; verified image deleted on promote failure); 137 tests pass; drift checker `No drift`; header md5 unchanged. **Verification found one defect in the packet and it was fixed rather than shipped:** `mkstemp` created the placeholder before the request, so `URLError` (printer off) and `HTTPError` (CDN 404) each left a zero-byte hidden `.<name>.<rand>.part` in the backup directory, and the retained image inherited `0600` instead of the umask default. The name is reserved and released instead; `test_download_that_never_starts_leaves_nothing_behind` covers both and is RED against the as-delivered source. Live `--test --reflash` returns 0 with the image retained under the configured root, an empty CWD, and md5 `c5306355c75a95fa6c04109c1d7f70b0`, both with the env var set and with the CWD fallback. |
| 3A — P4 integrity: sha256 sidecar, retained-copy state, change detection | **Done, committed `09278d0`** | Delivered by Crush (210s, exit 0), then independently verified. RED re-derived in a throwaway copy against the pre-packet source: 11 failed / 137 passed — **4 behavioural** (no sidecar written; identical bytes rewrote the image; changed bytes unreported; no fail-open warning) and **7 plain `AttributeError`** on the new symbols, which is existence rather than behaviour and is not evidence. Live cold/warm runs: cold retains and records (`sha256sum -c` → `OK`, md5 `c5306355…`), warm reports `Retained image matches its recorded digest: 8816a0e4fc6a` + `unchanged … Nothing rewritten` with image **and** record identical in inode/mtime/size; CWD empty, no `.part`/`.tmp` litter, zero non-ASCII. **No download-skipping branch exists on any path** (the warm run still fetched once, as specified). **Defect found in the packet and fixed rather than shipped:** `_sha256_file(part_filename)` ran unguarded at promote time, so an `OSError` there escaped as a traceback → `EXIT_ERROR` (1) where the pre-packet contract and the adjacent `os.replace` handler both return `EXIT_DOWNLOAD` (6); guarded, with a regression test that dies with the raw `OSError` when the guard is reverted. |
| 3B — P4 bandwidth: conditional GET reuse | **Dispatched** — handoff `docs/handoff-packet-3b-2026-09-13.md` | `If-None-Match`/`If-Modified-Since` from a persisted `<name>.validator` (the CDN `ETag`, else `Last-Modified`); `304` ⇒ reuse and log the decision loudly; `200` ⇒ download as today. Reuse is permitted only when 3A says `verified`. **Trap verified before writing the handoff:** `urllib` raises `HTTPError` for a `304` rather than returning a response, so the packet's naive form falls into the existing `except HTTPError` → `EXIT_DOWNLOAD` and reports a *successful* reuse as a download failure; the reproducer is in the handoff. |
| 4 — P6/P7 stream guards + bounded walk | Not started | |
| 5 — P8 hygiene | Not started | |
| P2/P3 — signals, deployment contract | Human-only, gated on G1/G4 | |

**Known drift, 2 lines, pending owner approval.** After Packet 3A the true counts are **149 tests**
and `TestRetainedCopyChangeDetection` = **6**; `AGENTS.md` still claims 148 and 5, because it is a
protected agent-instruction file and the approval prompt went unanswered — silence is not consent,
so the edit was left rather than routed around. `check_agents_md.py` reports exactly those two lines
until Packet 3B's session brings the counts current alongside its own test additions. Do not
"resolve" this by deleting a test: the missing guard test is what caught the unguarded digest read.

Packet 1 also corrected two errors in the handoff itself, worth carrying forward:

1. **The printed-site list was 16 of 17.** The `stderr` site (originally line 1049, now 1054) was wrongly marked "safe" — `stderr` is equally non-UTF-8, so it is in scope.
2. **A test that is RED for the wrong reason looks identical to one that is RED correctly.** The first attempt's forced-upload test failed `assert 4 == 7` because a pre-existing helper patched the stdlib `socket.socket` class, breaking asyncio's self-pipe (`EPERM` → `EXIT_PRINTER` before the upload window). The fix was to patch a `SimpleNamespace` onto `oh.socket` and to assert the upload window was reached. Always read *why* a test is red.

A pre-existing helper also left `tests/test_zzdbg.py` behind in an earlier session: 4 tests that each call `main()` against the real network, taking the suite from **0.27s to 271s**. Removed. Check suite wall time after any agent run, not just the pass count.

## Standing constraints (apply to every packet)

1. **Licence:** `head -15 oh_brother.py | md5sum` must stay `37f53a239b86f53d385d45047b8ceb07`. Never touch lines 1–15.
2. **Frozen contracts:** the CLI surface (positional IP, `-v -c -m -C -f -t --beta -p -y --reflash --version`) and the exit codes
   (0 OK, 1 ERROR, 2 USAGE, 3 CURRENT, 4 PRINTER, 5 VENDOR, 6 DOWNLOAD, 7 UPLOAD, 8 UNVERIFIED, 9 REFUSED, 130 INTERRUPTED).
   No renumbering, no new code without an explicit packet instruction.
3. **Dependencies:** stdlib + `pysnmp>=7.1.0,<8` only. No new runtime dependency, ever.
4. **Docs are asserted by a checker.** `python3 scripts/check_agents_md.py` verifies test count, class count, per-class
   table, exit codes, CLI flags, constant count (`**Module constants (N):**`), module version, licence hash and
   superseded phrasing. If your change moves any count, update `AGENTS.md` (and `README.md` where it states counts)
   **in the same commit**, then re-run the checker until it reports `No drift`.
5. **Verify, don't assert.** Every packet ends with the real outputs of:
   ```bash
   cd ~/Documents/Projects/oh-brother
   python3 -m pytest tests/ -q
   python3 scripts/check_agents_md.py
   npx --yes aislop@latest scan --changes --json | jq '{score, summary, byKind: .findingAssessment.byKind}'
   ```
   Report the actual output. A packet is not done because the code looks right.
6. **One packet per session.** Do not chain packets; do not "helpfully" fix something you noticed next door —
   report it instead.

---

## Packet 1 — P1: non-ASCII stdout laundering the exit-code contract

**Why first:** the only finding where a safety message is lost *and* the documented exit code is silently violated, and it is
verifiable without hardware.

**Evidence to reproduce first (RED):** forcing the `UPLOAD_INCOMPLETE` branch, `main()` returns `EXIT_UPLOAD` (7) on a UTF-8
stdout but `EXIT_ERROR` (1) on an ASCII stdout, with `TRANSFER INCOMPLETE — DO NOT POWER OFF` replaced by a
`UnicodeEncodeError` traceback at `:900`. Reproducer is in the audit's appendix; the assertion to pin is
"no raise **and** the return value stays 7".

**Scope:**
- Make the printed strings ASCII-only at the 16 stdout sites:
  `463, 475, 489, 604, 612, 620, 624, 730, 756, 759, 784, 791, 799, 861, 904, 1126`.
  Replace U+2014 with `--` (or `:` where it reads better). Leave em dashes in comments and docstrings — they are never printed.
- Close the class at the boundary in `main()`, before any output is emitted:
  ```python
  for _stream in (sys.stdout, sys.stderr):
      if isinstance(_stream, io.TextIOWrapper):
          try:
              _stream.reconfigure(errors='backslashreplace')
          except (ValueError, OSError):
              pass
  ```
  `backslashreplace`, **not** `replace` (which would degrade a safety warning to `?` on exactly the affected platforms);
  `isinstance` guard is required because `sys.stdout` is not always a `TextIOWrapper`.

**Tests:** add cases to `tests/test_oh_brother.py` that (a) assert each operator-facing message constant encodes with
`.encode('ascii')`, and (b) drive the forced `UPLOAD_INCOMPLETE` path under an ASCII stdout and assert no raise **and**
`EXIT_UPLOAD`. Assert the `DO NOT POWER OFF`-class substring, not the glyph. Show the new test failing before the fix.

**Do not:** change exit codes, change wording beyond the dash substitution, touch the licence header, reformat the file,
or "fix" the deliberate `except OSError: pass` in `_remove_quietly`.

---

## Packet 2 — P5: CWD-relative download with no lock and no early check

- Unique `.part` name (`mkstemp` or pid) so two concurrent runs cannot collide.
- Backup root resolved from an env var (`OH_BROTHER_BACKUP_DIR`) with the current CWD as fallback — an env var, not a
  CLI flag, so the frozen surface holds.
- Attempt directory creation **once after the consent gates and before the download (~`:744`)**, returning
  `EXIT_DOWNLOAD` with a clear message if it fails, instead of spending 15 MB first.
- Retention invariants unchanged: promote only after the integrity check, delete only after a verified flash, `--test` never deletes.

## Packet 3 — P4: stop overwriting the retained image; make the fetch stay honest

**Split into 3A and 3B** (handoffs: `handoff-packet-3a-2026-09-13.md`, `handoff-packet-3b-…`). The plan said
"land them separately"; the split is one-way dependent, so 3A is the only one that can go first.

**A correction found while writing the handoff, which reorders the two halves' importance.** A recorded digest
can prove the retained file is bit-for-bit the one that was verified, and can surface a changed vendor
artifact. It **cannot detect a corrupt download**, because the vendor supplies no expected digest — only the
declared `Content-Length`. So the sidecar's value is that it makes reuse safe to *consider*, and 3B is what
actually makes reuse *sound*, by corroborating with the vendor. The audit called the sidecar the
safety-relevant half; on this reading it is the enabling half, and neither ships alone.

**3A — integrity and change detection (no download skipping).** `sha256` sidecar written atomically at
retention time in `sha256sum` format; `_retained_copy_state()` classifying the retained copy as
`absent`/`verified`/`unrecorded`/`corrupt` (a malformed sidecar is `unrecorded`, never `corrupt`); identical
bytes ⇒ skip the pointless 15 MB rewrite; changed bytes ⇒ an unmissable notice naming **both** digests, then
promote as today. A missing sidecar is fail-open (re-download and re-verify) and never costs the image.
**3A must not skip a download on any path** — a reuse branch here would be exactly the gate weakening gate G2
forbids, with no vendor corroboration behind it. RED test first: an existing file whose **wrong** recorded
hash must still cause a real re-download.

**3B — conditional GET.** `If-None-Match`/`If-Modified-Since` from the retained file's recorded validator;
`304` ⇒ reuse and log the decision loudly; `200` ⇒ download as today. Verified live: the CDN returns `304`
with an `ETag` whose prefix is the md5 — but our sidecar records **sha256**, so the validator cannot be
derived from it and must be persisted as its own small file (the CDN's `ETag`, or `Last-Modified` when no
`ETag` is sent). That storage decision is the real work in 3B; the request itself is three lines. No validator
stored ⇒ no conditional request ⇒ today's behaviour. Reuse is permitted only when 3A says `verified`.

## Packet 4 — P6 + P7: stream guards and the one unbounded walk

- `_isatty(stream)` helper (`bool(stream) and stream.isatty()`) for the consent gate (~`:740`) and `prompt()` (`:265`); a
  `_flush()` guard for the six `sys.stdout.flush()` calls and `:795`. A missing stream means "not a terminal" — default-deny
  posture preserved, so a stream-less launch returns `EXIT_REFUSED` (9) rather than `EXIT_ERROR` (1).
- Wrap `_query_printer_version` (`:502`) in `asyncio.wait_for` to match `_printer_ready` (`:552`) and `main()` (`:1031`).
  Do not change `_snmp_walk_table`'s signature — 21 tests patch it wholesale.
- R16: TCP preflight connect before the fetch, scoped to `not args.test`, short timeout. Document that a successful connect
  proves the port is open, **not** that the printer will accept an image.

## Packet 5 — P8: hygiene (pure refactor, land last so it can be dropped)

Hoist `HTTP_TIMEOUT` and `MIN_FIRMWARE_SIZE` to module scope (**this moves the checker's constant count 30 → 31 — update
AGENTS.md in the same commit**); widen the artifact regex to 2–4 digits; collapse the duplicated fallback branches at
`:692-696`/`:700-704` while keeping both prints, the `EXIT_CURRENT` return, and `test_vcheck1_does_not_upload_without_reflash`'s
assertion that `_try_version_fallback` is **not** called; delete the unreachable `SystemExit` catch at `:503`; drop the dead
`ip` parameter in `_tcp_upload` (R18); widen `_version()`'s `except`; reorder the SSL guidance message at `:610-617`.

---

## Packet 6 — P2/P3: NOT a Crush packet

SIGTERM/SIGHUP semantics (P2) and the 66-minute deployment contract (P3) are hardware- and policy-coupled: the handler design
must be proven live under a real SIGTERM at both boundaries, and the doc must state a supervisor-grace requirement. Keep these
with the human, gated on G1/G4 in the audit. Nothing here should be delegated to an autonomous agent that cannot test the signal.

## Accepted findings — do not "fix"

Documented in the audit's §4 and the aislop evaluation §2: the module globals, the 293-line `update_firmware()`, its upstream
2-space indentation, the module-level `parser`, the per-walk `SnmpDispatcher`, the deliberate `except OSError: pass`, and the
`if name == …` SNMP parsing chain. Each has a stated reason and a trigger condition; a packet that changes one has failed.

**Last reviewed:** 2026-09-13
