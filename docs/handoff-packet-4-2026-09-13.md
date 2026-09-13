Implement Packet 4 from docs/plan-crush-handoff-2026-09-13.md in this repository.
This covers findings P6 (stream guards) and P7 (bounded walk, dispatcher lifetime, R16 preflight).
Packets 3A/3B are separate sessions and are already landed. Do not implement Packet 5 (P8).

Read first, before changing anything:
  docs/plan-crush-handoff-2026-09-13.md              (Packet 4 + the standing constraints)
  docs/audit-optimization-portability-2026-09-13.md  (sections P6 and P7)
  AGENTS.md                                          (image-retention invariant; testing section)

BASELINE: verify with `git log --oneline -4`, `python3 -m pytest tests/ -q` and
`python3 scripts/check_agents_md.py`. Keep the full suite passing and keep it FAST: the whole
suite currently runs in ~0.2s. A stray network-touching test once pushed it from 0.27s to 271s.
Measure the wall time before and after, and report both.

THE DEFECTS

P6: CPython sets sys.stdin/sys.stdout to None when the underlying fd is closed, and GUI-embedded
interpreters (pythonw.exe) get NULL standard handles. Then:
  - `prompt()` (:269-272) calls `sys.stdin.isatty()` -- AttributeError
  - the consent gate (:757) calls `sys.stdin.isatty()` -- AttributeError
  - `sys.stdout.flush()` and `sys.stdout.write('.')` -- AttributeError (print() to a None stdout
    is a silent no-op; flush() and write() are not)
A console-less launch therefore dies at the first flush inside the SNMP stage, is swallowed by
the top-level handler, and reports EXIT_ERROR (1) where the intended outcome is a clean
EXIT_REFUSED (9). Note the AttributeError from the download loop's flush/`write` is NOT caught
by that loop's `except (OSError, http.client.HTTPException)`.

P7: `_query_printer_version` (:512-526) is the only SNMP call without a timeout -- `_printer_ready`
(:568-571) and main() both wrap theirs in `asyncio.wait_for(..., SNMP_DEADLINE)`. This one is on
the flash-verification path, so one slow walk can blow a poll's budget. Also: nothing checks that
the printer is reachable before spending the ~15 MB download (R16).

DO EXACTLY THIS

1. `_isatty(stream)` -> `bool(stream) and stream.isatty()`. Use it in `prompt()` (:271) and in
   the consent gate (:757). A missing stream means "not a terminal", which PRESERVES the
   default-deny posture: the gate must return EXIT_REFUSED (9) exactly as it does for a
   non-TTY stdin, NOT EXIT_ERROR.

2. `_flush(stream=None)` that flushes only when the stream is truthy and has flush (default
   sys.stdout). Replace EVERY bare `sys.stdout.flush()` with it -- grep for the authoritative
   list, currently :692, :777, :840, :887, :1081, :1173 -- and guard the progress dot at :839-840
   so a None stdout skips the write entirely instead of raising. Do not change what is printed
   when stdout IS present, and do not change the progress-dot cadence.

3. Wrap the walk in `_query_printer_version` (:519) in `asyncio.wait_for(..., SNMP_DEADLINE)`,
   mirroring `_printer_ready` (:569-571) exactly:
       table = asyncio.run(asyncio.wait_for(
           _snmp_walk_table(ip, community, BROTHER_SNMP_OID), SNMP_DEADLINE,
       ))
   - Do NOT change `_snmp_walk_table`'s signature; 21 tests patch it wholesale.
   - Keep the function's documented contract: it still returns None when the printer cannot be
     reached or does not report the category. The existing `except (Exception, SystemExit)` at
     :520 already catches the TimeoutError -- leave that except clause ALONE, including the
     unreachable SystemExit member (removing it is Packet 5's job, not yours).
   - Do not let a timeout be reported as a version answer: `_verify_flash` (:529-559) must still
     classify an unreachable printer as 'unverified', never as 'mismatch'. Assert this.

4. R16 preflight -- WARN, NEVER REFUSE, and only on the TCP transport. This is a deliberate
   design decision; implement it as specified rather than upgrading it to a gate:
   - Scope: run it only when `not args.test` AND `not args.password`. `--password` (-p) selects
     the FTP transport, where a closed TCP 9100 means nothing at all; `--test` must still produce
     a backup whether or not the printer accepts uploads.
   - Helper `_printer_port_open(ip, port, timeout)`: a short TCP connect, returning bool, so tests
     can patch a single seam. Use a new module-level constant `PREFLIGHT_TIMEOUT = 5`.
   - On failure print ONE clear, ASCII-only warning naming the host and port and stating that the
     upload will likely fail, that the download will continue so the image is retained, and that
     an administrator password / FTP upload path is unaffected. Then CONTINUE.
   - Do NOT return an exit code from this path, do NOT abort the download, and do NOT describe it
     anywhere (code, comments, README) as an upload safety gate: a successful connect proves the
     port is open, not that the printer will accept an image. Refusing here was considered and
     rejected -- it would remove the retained-backup path for an offline printer, contradicting
     this tool's own retention invariant and the audit's constraint on R16.
   - SUITE HAZARD: this introduces a real TCP connect into a previously network-free code path.
     Any existing test that reaches the preflight must not attempt a real connection -- patch the
     seam in those tests. The suite must stay under ~1s; if it does not, find what is dialling out
     and fix the test, not the clock.

5. Dispatcher lifetime (P7's downgraded item): do NOT introduce a persistent dispatcher, and do
   NOT share one across `asyncio.run` calls. If you add a `try/finally: dispatcher.close()`
   around the construction in `_snmp_walk_table` (:1014), it must not change that function's
   signature or its observable behaviour, and it must not affect the 21 tests that patch it. This
   item is optional: skipping it entirely is an acceptable outcome, and the audit measured the
   churn as deferred release rather than a leak.

6. Unchanged: all exit-code values, the CLI surface, and the retention invariants.

TESTS -- write them RED FIRST, and prove RED before you touch the source

Add to tests/test_oh_brother.py. For each, paste the real pre-fix failure output:
  (a) `_isatty(None)` is False and `_isatty(<non-tty stream>)` is False, `_isatty(<tty>)` True.
  (b) The consent gate with sys.stdin None and no --yes returns EXIT_REFUSED (9) -- not
      EXIT_ERROR (1). This is the packet's headline assertion; print both codes in the RED run.
  (c) `prompt()` with sys.stdin None does not raise.
  (d) With sys.stdout None, a message-producing path runs to completion without AttributeError
      (drive the download path far enough to hit a flush and the progress dot).
  (e) `_query_printer_version` on a walk that never returns is bounded: patch `_snmp_walk_table`
      to a coroutine that awaits a long sleep and patch the deadline DOWN (e.g. `oh.SNMP_DEADLINE`
      to 0.05) so the test is fast, then assert the call returns None rather than hanging and
      that `_verify_flash` classifies it 'unverified' (never 'mismatch'). Do not add a test that
      sleeps for the real 30s deadline.
  (f) The preflight is skipped under `--test` and under `--password`, and is attempted otherwise.
      Assert with a patched seam and a call counter, not by dialling out.
  (g) A refused preflight warns and the run continues (no new exit code, download still happens).

DIAGNOSTIC TRAPS - read before writing tests

- Do NOT patch the stdlib `socket.socket` class to observe connections: asyncio's event loop
  builds its self-pipe from it, so the patched version makes `socket.socketpair()` return mock
  fds and `epoll.register` dies with `OSError: Operation not permitted`, which the SNMP stage maps
  to EXIT_PRINTER (4) -- a plausible-looking exit code for an unrelated reason. Patch your own
  helper (`oh._printer_port_open`) or a SimpleNamespace onto `oh.socket`.
- Do NOT patch `asyncio.wait_for` globally; patch the module's own helper or the deadline constant.
- ALWAYS read WHY a test failed, not just that it did. A failure that names the defect is the bar;
  an AttributeError on a symbol you just introduced means the test is new, not that it is red.

HARD CONSTRAINTS - a change outside this list fails the task

- Do NOT modify lines 1-15 of oh_brother.py (GPLv2 header). Verify afterwards:
  head -15 oh_brother.py | md5sum   must equal   37f53a239b86f53d385d45047b8ceb07
- Do NOT change any exit-code value, and do NOT add or rename any CLI flag.
- Do NOT add any dependency: stdlib + pysnmp only.
- Do NOT reformat, re-indent, reorder or "tidy" the file. update_firmware() keeps its upstream
  2-space indentation.
- Do NOT act on any aislop finding, and do NOT remove the existing aislop-ignore directives.
- Do NOT touch the following, all of which are later packets or accepted findings: the artifact
  version regex, MIN_FIRMWARE_SIZE / the inlined `timeout=30` values, the duplicated fallback
  branches, the `_tcp_upload` `ip` parameter, `_version()`'s except clause, the SSL guidance
  text, the module globals, and the 293-line update_firmware().
- Create NO new files in the repository. Scratch work goes in /tmp only.
- Make no other changes anywhere. If you spot another bug, report it in your summary instead of
  fixing it.

DOCS THE CHECKER ASSERTS

PREFLIGHT_TIMEOUT moves the module constant count by one. Get the real current value from
`python3 scripts/check_agents_md.py`, update `**Module constants (N):**` in AGENTS.md (and
README.md if it states the count) IN THE SAME CHANGE, then re-run until it prints "No drift".
Document the preflight in README.md where the download step is described, in the same change,
using the honest wording from point 4 (a warning about reachability, not an upload safety gate).
Note the checker also fails on a backticked identifier in AGENTS.md that starts with "_" or is
ALL-CAPS and does not exist in oh_brother.py -- describe such things in words.

VERIFY AND REPORT

Run these and paste their real, unedited output in your final summary:
  python3 -m pytest tests/ -q
  python3 -m pytest tests/ -q --collect-only | tail -2
  python3 scripts/check_agents_md.py
  head -15 oh_brother.py | md5sum
  npx --yes aislop@latest scan --changes --json | jq '{score, summary, byKind: .findingAssessment.byKind}'
  git status --porcelain
  git diff --stat
Report the full-suite wall time before and after, and state explicitly that no test dials out.

Leave no new untracked files behind. Finish your summary with: files changed, the exact
before/after behaviour for each test case, the full-suite wall time, and the exact commands run.
