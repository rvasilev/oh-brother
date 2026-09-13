Implement Packet 3B from docs/plan-crush-handoff-2026-09-13.md in this repository.
This is the SECOND HALF of Packet 3 (finding P4) and it CONSUMES the state Packet 3A records.
The other halves of Packet 4/Packet 5 are separate sessions. Do not implement them here.

Read first, before changing anything:
  docs/plan-crush-handoff-2026-09-13.md              (Packet 3, incl. the 3A/3B split note)
  docs/audit-optimization-portability-2026-09-13.md  (section P4)
  docs/handoff-packet-3a-2026-09-13.md               (what 3A already built, and its state names)
  AGENTS.md                                          (image-retention invariant; testing section)

BASELINE: Packet 3A is committed. Verify with `git log --oneline -3` and `grep -n
'_retained_copy_state\|_sha256_file\|_sidecar_path' oh_brother.py`. `_retained_copy_state()`
returns `(state, digest_or_None)` with state in
{'absent','verified','unrecorded','corrupt'}. Keep every 3A test passing unchanged.

THE DEFECT

Every run re-fetches an artifact it already holds in verified form (15,270,855 bytes measured).
The CDN supports conditional requests, so the 15 MB is avoidable *when the vendor confirms the
retained copy is current*. The vendor's confirmation is what makes reuse sound: 3A's sidecar
proves only that the file on disk is the file that was verified, never that the vendor still
serves those bytes.

DO EXACTLY THIS

1. Persist the vendor validator at retention time. Add `VALIDATOR_SUFFIX = '.validator'` as a
   module-level constant next to SHA256_SUFFIX, plus `_validator_path(backup_path)`.

   File content: exactly one line, `<Header-Name>: <value>` verbatim from the download
   response -- `ETag: "5f8a-1c2d"` if the response carried an ETag, otherwise
   `Last-Modified: <the raw HTTP date>`. If the response carried neither, write NO file (the
   absence is meaningful: it means "the vendor gave us nothing to condition on").

   (Re)write it after every successful body download, INCLUDING when 3A finds the bytes
   identical and skips rewriting the 15 MB image: the validator is a few bytes, and a stale
   validator would silently disable conditional requests. Use the same atomic write
   (temp file + os.replace in the same directory) that 3A uses for the sidecar.

2. Conditional request. When -- and only when -- all of these hold:
     - `_retained_copy_state(backup_path)` is 'verified' (3A's judgement, not a new one);
     - a validator file exists and parses;
   add the header to the fetch's Request:
     - `ETag`       -> `If-None-Match: <value>`
     - `Last-Modified` -> `If-Modified-Since: <value>`
   Otherwise do not add any conditional header. Note: urllib normalises the name on the wire
   (`If-none-match`, capital first letter only) and servers match case-insensitively -- that is
   correct as-is, do not "fix" it.

3. **THE TRAP, verified empirically before this handoff was written -- read it twice.**
   `urllib` raises `HTTPError` for a 304; it does NOT return a response object. Reproducer
   (run it): a local server answering 304 to a matching `If-None-Match` gives
   `raised HTTPError, code = 304 | reason: Not Modified`, and `isinstance(e, URLError)` is True.

   So a conditional request falls straight into the existing handler at the `except
   urllib.error.HTTPError as e:` block, which prints "HTTP 304 (Not Modified) from Brother CDN
   -- try again later." and returns EXIT_DOWNLOAD -- i.e. a *successful* reuse would be
   reported as a download failure. Handle `e.code == 304` BEFORE the generic HTTPError path,
   and take the reuse branch there.

4. The reuse branch: `304` means the retained image is current.
     - Do not read a body. Do not create, write, or touch the partial file -- if you created
       one, remove it before returning (3A's mkstemp reservation releases the name
       immediately, so there is normally nothing to remove; assert the state anyway).
     - Set `filename = backup_path` and continue exactly where a successful download would have
       continued: the `--test` early return (which must report the retained path) or the upload
       path.
     - Print one loud, ASCII-only line saying the vendor confirmed the retained image is
       current (HTTP 304), naming the path and stating that no download was performed. The
       decision must be visible in the log, not silent.
     - Do not rewrite the sidecar; the bytes did not change.

5. Fail-open everywhere:
     - A `200` response behaves exactly as today (download, verify, promote).
     - A `304` whose retained copy is 'verified' by 3A but which fails a re-check at that
       moment (the `304` arrived, but the file no longer matches its sidecar) must fall back to
       a full unconditional download, not abort.
     - Any other status, or a malformed validator file, behaves as today.

6. Unchanged invariants (a violation fails the packet): promote only after the integrity check
   passes; delete the image only after a verified successful flash; `--test` never deletes; the
   partial file is never visible under a real firmware name.

TESTS -- write them RED FIRST, and prove RED before you touch the source

Add to tests/test_oh_brother.py. For each, paste the real pre-fix failure output:
  (a) 304 reuse: `urlopen` raises HTTPError(304) -> the run reuses the retained image, reads no
      body, performs no download, and reaches the same outcome as a successful download
      (`--test` returns EXIT_OK and names the retained path). Assert the retained file is
      untouched (mtime and inode unchanged).
  (b) The trap, pinned as a regression test: with a 304 the run must NOT return EXIT_DOWNLOAD
      and must NOT print the "try again later" message.
  (c) No validator file -> NO conditional header is sent (assert the built Request's headers
      carry neither If-None-Match nor If-Modified-Since), and the download proceeds.
  (d) `_retained_copy_state` != 'verified' (e.g. 'corrupt', with the wrong recorded hash) ->
      no conditional header and a real download. This is 3A's G2 case carrying through: a
      wrong recorded hash must never produce reuse.
  (e) 200 with a validator present -> normal download path, unchanged behaviour.
  (f) 304 arriving while the retained copy fails re-verification -> falls back to a full
      download (fail-open), and does not abort.
  (g) The validator file is written from the response headers at retention, in the documented
      one-line `<Header-Name>: <value>` form, and is rewritten when the bytes are identical.
  (h) A response with neither ETag nor Last-Modified writes no validator file.

DIAGNOSTIC TRAPS - read before writing tests

- To drive the download, patch `oh.urllib.request.urlopen` (the module's own binding). Do NOT
  patch the stdlib `socket.socket` class: asyncio builds its event loop self-pipe from it, so
  the patched version makes `socket.socketpair()` return mock fds and `epoll.register` dies
  with `OSError: Operation not permitted`, which the SNMP stage maps to EXIT_PRINTER (4) -- a
  plausible-looking exit code for an unrelated reason.
- Do NOT patch `hashlib.sha256` globally; 3A's helpers are patchable by name.
- ALWAYS read WHY a test failed, not just that it did. A failure that names the defect (the
  status code, the header, the path) is the bar; an AttributeError on a symbol you just
  introduced means the test is new, not that it is red.

HARD CONSTRAINTS - a change outside this list fails the task

- Do NOT modify lines 1-15 of oh_brother.py (GPLv2 header). Verify afterwards:
  head -15 oh_brother.py | md5sum   must equal   37f53a239b86f53d385d45047b8ceb07
- Do NOT change any exit-code value, and do NOT add or rename any CLI flag.
- Do NOT add any dependency: stdlib + pysnmp only.
- Do NOT reformat, re-indent, reorder or "tidy" the file. update_firmware() keeps its
  upstream 2-space indentation.
- Do NOT act on any aislop finding, and do NOT remove the existing aislop-ignore directives.
- Do NOT change 3A's classification semantics or its four state names.
- Create NO new files in the repository. Scratch work goes in /tmp only.
- Make no other changes anywhere. If you spot another bug, report it in your summary instead
  of fixing it.

DOCS THE CHECKER ASSERTS

VALIDATOR_SUFFIX moves the module constant count by one. Get the real current value from
`python3 scripts/check_agents_md.py`, update `**Module constants (N):**` in AGENTS.md (and
README.md if it states the count) IN THE SAME CHANGE, then re-run until it prints "No drift".
Document the validator file's format and the reuse rule in README.md's firmware-retention
paragraph alongside 3A's sidecar, in the same change. Note the checker also fails on a
backticked identifier in AGENTS.md that starts with "_" or is ALL-CAPS and does not exist in
oh_brother.py -- describe such things in words.

VERIFY AND REPORT

Run these and paste their real, unedited output in your final summary:
  python3 -m pytest tests/ -q
  python3 -m pytest tests/ -q --collect-only | tail -2
  python3 scripts/check_agents_md.py
  head -15 oh_brother.py | md5sum
  npx --yes aislop@latest scan --changes --json | jq '{score, summary, byKind: .findingAssessment.byKind}'
  git status --porcelain
  git diff --stat

Then prove the reuse against the real CDN, because a mocked 304 cannot show the vendor actually
answers one:
  (1) run once to ensure a retained image and validator exist:
      OH_BROTHER_BACKUP_DIR=/tmp/p3b-live timeout 300 python3 oh_brother.py --test --reflash 192.168.88.65
  (2) run it AGAIN and paste the output: the second run must reuse, print its decision line, and
      exit 0.
  (3) paste `ls -l` of the backup directory and `cat` of the validator file.
  (4) report the wall time of each run -- the second must be markedly faster, which is the
      point of the packet.
This is non-destructive and retains the image. If the CDN does NOT answer 304 (it may serve a
different ETag per request), say so plainly with the raw evidence rather than tuning the test to
pass, and report what the reuse decision degrades to.

Leave no new untracked files behind. Finish your summary with: files changed, the exact
before/after behaviour for each test case, the full-suite wall time, and the exact commands run.
