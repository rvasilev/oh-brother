Implement Packet 2 from docs/plan-crush-handoff-2026-09-13.md in this repository.

Read first, before changing anything:
  docs/plan-crush-handoff-2026-09-13.md              (Packet 2 + the standing constraints)
  docs/audit-optimization-portability-2026-09-13.md  (section P5)
  AGENTS.md                                          (image-retention invariant; testing section)

BASELINE: Packet 1 is committed at 4b4c054. 130 tests pass in ~0.2s, check_agents_md.py
reports "No drift", and the working tree is clean. Treat 130 as the baseline; keep the
complete suite passing.

THE DEFECT (three parts, one packet)

Where an image lands is decided late, and named predictably:

- `part_filename = filename + '.part'` (oh_brother.py:751) is built in the CURRENT WORKING
  DIRECTORY and is fully predictable. Two runs started from the same directory - or one run
  and a leftover partial from a previous crash - collide on that exact name, so one run
  truncates or `_remove_quietly`s the other's in-flight download.
- The retained location is `BACKUP_DIRNAME = 'firmware_backups'` (oh_brother.py:121), joined
  in `_firmware_backup_path()` (:436-449) RELATIVE TO CWD. There is no way to say "store
  backups on my data disk" without cd'ing somewhere first.
- `os.makedirs(...)` for the backup directory happens at :816 - AFTER the ~15 MB download,
  inside the promote step. A backup location that cannot be created therefore costs a full
  download before it is discovered, and the operator learns about it last.

DO EXACTLY THIS

1. Resolve the backup root from an environment variable, OH_BROTHER_BACKUP_DIR, falling back
   to the current working directory (today's behaviour). Add it as a module-level constant
   next to BACKUP_DIRNAME (e.g. `BACKUP_DIR_ENV = 'OH_BROTHER_BACKUP_DIR'`) rather than an
   inline literal. It MUST be an environment variable, NOT a CLI flag - the CLI surface is
   frozen (see constraints).

   The retained layout underneath the root is unchanged:
       <root>/firmware_backups/<MODEL>/<version>/<filename>
   `_firmware_backup_path()` is used by `_retained_message()` and asserted by existing tests;
   keep its documented shape and its sanitizing behaviour exactly.

2. Give the partial file a unique name so concurrent runs cannot collide. An unpredictable
   name per invocation is required (mkstemp in the target directory, or the pid plus a random
   suffix) - a fixed suffix like '.part' is the bug. Keep the retention invariant: the
   partial file must never be visible under a real firmware name.

   Prefer writing the partial into the resolved backup directory rather than CWD. Two
   reasons, and both are requirements, not preferences:
     (a) it keeps `os.replace()` a same-directory rename, which is atomic and cannot fail
         with EXDEV - see the next point;
     (b) it stops the run from littering whatever directory it was launched from.
   If you keep the partial in CWD instead, you must handle cross-device promotion - see (3).

3. `os.replace(part_filename, backup_path)` (:817) is currently safe only because both ends
   sit under CWD. Once the backup root can point at another filesystem, a cross-device rename
   raises OSError(EXDEV) and the retention invariant is what breaks: the image exists only as
   a partial file, and the operator is told the backup failed while the upload may already be
   in progress. Whichever layout you choose, promotion must not assume same-filesystem, and a
   promotion failure must keep the image recoverable and return EXIT_DOWNLOAD with a message
   that says where the file actually is. Never delete a verified image because promotion
   failed.

4. Attempt backup-directory creation ONCE, after the consent gates and before the download
   starts - i.e. between the REFUSED gate at :741-745 and the `urlopen` at :753-755. If the
   directory cannot be created, print a clear message naming the resolved path, return
   EXIT_DOWNLOAD, and do not open a socket or download a single byte.

5. Unchanged: promote only after `_verify_firmware_integrity()` passes; delete the image only
   after a verified successful flash; `--test` never deletes and always retains.

TESTS - write them RED FIRST, and prove RED before you touch the source

Add to tests/test_oh_brother.py. For each, paste the real pre-fix failure output:
  (a) An unusable backup directory (e.g. OH_BROTHER_BACKUP_DIR pointed at a path under a
      read-only temp directory, or a path where a FILE already occupies the directory name)
      returns EXIT_DOWNLOAD, AND `urlopen` was never called - assert the call count, and
      assert no socket was created. Asserting only the exit code is not enough: a run that
      downloaded 15 MB first also returns EXIT_DOWNLOAD.
  (b) OH_BROTHER_BACKUP_DIR is honoured: with it set to tmp_path, the retained image lands
      under tmp_path/firmware_backups/<MODEL>/<version>/, and nothing is written to CWD.
  (c) With the variable unset, the root falls back to CWD (today's behaviour) - this is the
      regression guard for existing users.
  (d) Two invocations cannot share a partial filename: create a stale partial with the OLD
      fixed name first, then run the download path and assert the run's own partial name
      differs from it / that the stale file is untouched by this run.
  (e) Promotion failure keeps the verified image recoverable and returns EXIT_DOWNLOAD (patch
      `os.replace` to raise OSError, then assert the image still exists on disk).

DIAGNOSTIC TRAP - read this before writing test (a)

To prove "no download happened" you will be tempted to patch the stdlib `socket.socket`
class. DO NOT. asyncio's event loop builds its own self-pipe from that class, so the patched
version makes `socket.socketpair()` return mock fds; `epoll.register` then receives a
regular-file descriptor and dies with `OSError: Operation not permitted`, which the SNMP
stage catches and maps to EXIT_PRINTER (4). You would then be looking at a plausible-looking
exit code for a completely unrelated reason - the exact trap that made Packet 1's first
attempt red for the wrong reason. Patch `oh.urllib.request.urlopen` (the module's own
binding), or a `SimpleNamespace` onto `oh.socket`, and leave the stdlib module alone.

ALWAYS read WHY a test failed, not just that it did. `assert 6 == 6` proves nothing. A
failure that names the defect (the call count, the path, the exit code on the specific path)
is the bar; `AttributeError` on a symbol you just introduced means the test is new, not that
it is red.

HARD CONSTRAINTS - a change outside this list fails the task

- Do NOT modify lines 1-15 of oh_brother.py (GPLv2 header). Verify afterwards:
  head -15 oh_brother.py | md5sum   must equal   37f53a239b86f53d385d45047b8ceb07
- Do NOT change any exit-code value, and do NOT add or rename any CLI flag. The backup root
  is configured by environment variable only.
- Do NOT add any dependency: stdlib + pysnmp only.
- Do NOT reformat, re-indent, reorder or "tidy" the file. update_firmware() keeps its
  upstream 2-space indentation.
- Do NOT act on any aislop finding. The swallowed `except OSError: pass` in `_remove_quietly`,
  the module globals, the module-level `parser` and the `if name == ...` SNMP chain are
  documented as accepted in the audit's section 4. A packet that changes one has failed.
- Create NO new files in the repository. Scratch work goes in /tmp only. Do not leave a
  tests/test_zzdbg.py-style helper behind (a previous one added 4 network-touching tests and
  pushed the suite from 0.3s to 271s).
- Make no other changes anywhere. If you spot another bug, report it in your summary instead
  of fixing it.

VERIFY AND REPORT

Run these and paste their real, unedited output in your final summary:
  python3 -m pytest tests/ -q
  python3 -m pytest tests/ -q --collect-only | tail -2
  python3 scripts/check_agents_md.py
  head -15 oh_brother.py | md5sum
  npx --yes aislop@latest scan --changes --json
  git status --porcelain
  git diff --stat

If your change moves any count the checker asserts, update AGENTS.md in the same change and
re-run the checker until it prints "No drift". This packet is likely to move the module
constant count (adding BACKUP_DIR_ENV takes the documented 30 to 31) - update
`**Module constants (N):**` and any count in README.md if you add or remove a constant.
Note the checker also fails on a backticked identifier in AGENTS.md that starts with "_" or is
ALL-CAPS and does not exist in oh_brother.py - describe such things in words.

Leave no new untracked files behind. Finish your summary with: files changed, the exact
behaviour before/after for each of the five test cases, the full-suite wall time, and the
exact commands run.
