Implement Packet 5 from docs/plan-crush-handoff-2026-09-13.md in this repository.
This covers finding P8 (hygiene). It is a pure refactor and lands LAST, so it can be dropped
without consequence. Packets 3A/3B and 4 are separate sessions and are already landed.

Read first, before changing anything:
  docs/plan-crush-handoff-2026-09-13.md              (Packet 5 + the standing constraints)
  docs/audit-optimization-portability-2026-09-13.md  (section P8)
  AGENTS.md                                          (image-retention invariant; testing section)

BASELINE: verify with `git log --oneline -4`, `python3 -m pytest tests/ -q` and
`python3 scripts/check_agents_md.py`. This packet changes NO behaviour; every one of the
following must hold identically before and after: the exit codes, the printed messages, the CLI
surface, and the retention invariants. If a change would alter any of them, stop and report it
instead of making it.

DO EXACTLY THIS - eight items, all behaviour-preserving

1. Hoist `MIN_FIRMWARE_SIZE = 102400` (currently a local inside `_verify_firmware_integrity`,
   ~:413) to module scope with the other constants near DOWNLOAD_CHUNK/DOWNLOAD_HARD_CAP, and
   use the module-level name. Keep the comparison and both printed messages byte-identical.

2. Add `HTTP_TIMEOUT = 30` and use it for the three inlined `timeout=30` sites: the defaults of
   `_http_post` (~:600) and `_http_request` (~:609), and the download's `urlopen` (~:798).
   AGENTS.md states every timeout is a named constant; this is the one that broke that claim.

3. Widen the artifact-version regex `_ARTIFACT_VERSION_RE` (~:323) from `_(\\d{3})([A-Za-z])` to
   `_(\\d{2,4})([A-Za-z])`. The audit verified the current pattern returns None for the 2- and
   4-digit forms (`X_12Q`, `Y_1245Q`), which silently lands in the warn-and-proceed branch -- the
   unsafe direction for a downgrade gate. A single-digit form (`V_1Q`) stays unparsed BY DESIGN;
   do not widen to `\\d+`.

4. Collapse the duplicated fallback branches (~:709-713 and ~:717-721), which are byte-identical.
   The shape that preserves everything:
       if result['version_check'] == '1':
         print('Firmware already up to date')
         if not getattr(args, 'reflash', False):
           # R1: already current is terminal unless --reflash was given.
           return EXIT_CURRENT
         use_fallback = True
       elif result['firmware_url'] is None:
         print('No firmware update info path found '
               '(newer Brother models require version fallback)')
         use_fallback = True
       else:
         use_fallback = False
         firmwareURL = result['firmware_url']
       if use_fallback:
         firmwareURL = _try_version_fallback(version, cat, url, hdrs)
         if firmwareURL:
           print('Found firmware URL via version fallback')
         else:
           return EXIT_VENDOR
   BOTH prints, the EXIT_CURRENT return, and the EXIT_VENDOR returns must survive. The guard test
   `test_vcheck1_does_not_upload_without_reflash` must still prove that `_try_version_fallback`
   is NOT called when VCHECK=1 and --reflash is absent -- keep that assertion working, and keep
   the comment that explains the order.

5. Remove the unreachable `SystemExit` member from the catch in `_query_printer_version`
   (~:520), leaving `except Exception:`. Nothing in that chain raises SystemExit;
   `_snmp_walk_table` raises SnmpError, which is an Exception. The function must still return
   None on failure -- that contract is asserted by tests.

6. Drop the dead `ip` parameter from `_tcp_upload` (~:367) and fix its call site (~:913).
   **This one has a test cost you must pay in the same change.** Grep first:
       grep -rn '_tcp_upload' tests/test_oh_brother.py
   There are direct calls with three arguments (`oh._tcp_upload(str(fw_path), "1.2.3.4",
   mock_sock)`) and six `monkeypatch.setattr(oh, "_tcp_upload", lambda f, ip, sock: ...)` patches
   to update. A patched lambda whose arity no longer matches the call site raises TypeError, which
   will look like a mysterious failure far from the change -- make the arities agree.

7. Widen `_version()`'s except (~:228-231) beyond `PackageNotFoundError` so a malformed-metadata
   `ImportError` cannot make the module un-importable. Keep the returned fallback string
   byte-identical. This is the only import-time item worth doing; leave the rest of that area
   alone.

8. Reorder the SSL guidance message (~:629-633) so it leads with the platform remedy and mentions
   `SSL_CERT_FILE`. `urllib` consults OpenSSL's default trust paths, NOT `certifi`, so the
   current "Or: pip install certifi" line does not repoint trust on its own. Suggested shape,
   ASCII-only (Packet 1's rule):
       Linux: install your distribution's ca-certificates package
       macOS: run Install Certificates.command in your Python installation
       FreeBSD: install ca_root_nss, or set SSL_CERT_FILE to a CA bundle path
   Keep the surrounding message's meaning and any icon/prefix formatting; change only the
   guidance content and its order.

Do not do anything else in this packet -- no reformatting, no reindentation, no renaming, no
"while I was here" cleanup.

TESTS

This packet is behaviour-preserving, so it adds tests only where it fixes a real defect:
  (a) `_ARTIFACT_VERSION_RE` parses the 2-, 3- and 4-digit forms, and still declines the
      single-digit form. Include the exact strings from the audit: `X_12Q`, `Y_1245Q`, `V_1Q`.
  (b) `_tcp_upload` has no `ip` parameter (assert by signature inspection) and its call site
      still works -- the upload tests passing is that assertion.
  (c) `_version()` returns its fallback string rather than raising when the distribution
      metadata is malformed (patch `oh._dist_version` to raise ImportError).
  (d) The pre-existing guard test for the collapsed branches still passes UNCHANGED. Do not
      weaken it to accommodate the refactor.
If a test you did not expect to touch starts failing, that is a signal the refactor changed
behaviour -- read WHY it failed and report it rather than editing the test.

DIAGNOSTIC TRAPS

- Do NOT patch the stdlib `socket.socket` class in any new test: asyncio builds its event loop
  self-pipe from it, so the patched version makes `socket.socketpair()` return mock fds and
  `epoll.register` dies with `OSError: Operation not permitted`, which the SNMP stage maps to
  EXIT_PRINTER (4) -- a plausible-looking exit code for an unrelated reason.
- ALWAYS read WHY a test failed. An arity TypeError on `_tcp_upload` means you missed a patch
  site; an AttributeError on a symbol you just introduced means the test is new, not that it is red.

HARD CONSTRAINTS - a change outside this list fails the task

- Do NOT modify lines 1-15 of oh_brother.py (GPLv2 header). Verify afterwards:
  head -15 oh_brother.py | md5sum   must equal   37f53a239b86f53d385d45047b8ceb07
- Do NOT change any exit-code value, and do NOT add or rename any CLI flag.
- Do NOT add any dependency: stdlib + pysnmp only.
- Do NOT reformat, re-indent, reorder imports or "tidy" the file. update_firmware() keeps its
  upstream 2-space indentation. The module's own indentation in that function is not uniform --
  leave it alone.
- Do NOT act on any aislop finding, and do NOT remove the existing aislop-ignore directives.
- Do NOT touch the accepted findings (audit section 4): the module globals, the 293-line
  update_firmware(), the module-level parser, the per-walk SnmpDispatcher, the deliberate
  `except OSError: pass` in `_remove_quietly`, the `if name == ...` SNMP chain.
- Create NO new files in the repository. Scratch work goes in /tmp only.
- Make no other changes anywhere. If you spot another bug, report it in your summary instead of
  fixing it.

DOCS THE CHECKER ASSERTS

Hoisting MIN_FIRMWARE_SIZE and adding HTTP_TIMEOUT moves the module constant count (expect +2).
Get the real current value from `python3 scripts/check_agents_md.py`, update
`**Module constants (N):**` in AGENTS.md (and README.md if it states the count) IN THE SAME
CHANGE, then re-run until it prints "No drift". AGENTS.md also claims every timeout is a named
constant -- that claim becomes true with this packet, so check whether its wording needs to
change from an aspiration to a statement. Note the checker also fails on a backticked identifier
in AGENTS.md that starts with "_" or is ALL-CAPS and does not exist in oh_brother.py.

VERIFY AND REPORT

Run these and paste their real, unedited output in your final summary:
  python3 -m pytest tests/ -q
  python3 -m pytest tests/ -q --collect-only | tail -2
  python3 scripts/check_agents_md.py
  head -15 oh_brother.py | md5sum
  grep -n 'timeout=30' oh_brother.py
  npx --yes aislop@latest scan --changes --json | jq '{score, summary, byKind: .findingAssessment.byKind}'
  git status --porcelain
  git diff --stat
Then a live end-to-end run to prove the refactor is behaviour-preserving in the real path:
  OH_BROTHER_BACKUP_DIR=/tmp/p5-live timeout 300 python3 oh_brother.py --test --reflash 192.168.88.65
Paste its real output and the retained image's md5 (the known-good value is
c5306355c75a95fa6c04109c1d7f70b0). This is non-destructive.

Leave no new untracked files behind. Finish your summary with: files changed, the exact
before/after behaviour for the eight items, the full-suite wall time, and the exact commands run.
