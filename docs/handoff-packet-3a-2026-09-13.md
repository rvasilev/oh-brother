Implement Packet 3A from docs/plan-crush-handoff-2026-09-13.md in this repository.
This is the FIRST HALF of Packet 3 (finding P4). The second half (conditional GET) is a
separate session and depends on the state this packet records. Do not implement it here.

Read first, before changing anything:
  docs/plan-crush-handoff-2026-09-13.md              (Packet 3 + the standing constraints)
  docs/audit-optimization-portability-2026-09-13.md  (section P4)
  AGENTS.md                                          (image-retention invariant; testing section)

BASELINE: Packet 2 is committed at c8c85ed. 137 tests pass in ~0.2s, check_agents_md.py
reports "No drift", and the working tree is clean. Treat 137 as the baseline.

THE DEFECT

`firmware_backups/` is the only copy of the image that already passed verification, and
nothing records what that copy is:

- `_verify_firmware_integrity()` (oh_brother.py:407) is framing, not integrity: declared
  Content-Length equality plus a 100 KB floor. A correct-length body with wrong content
  passes it and then replaces a retained copy that had already passed.
- `os.path.exists(` appears nowhere in the module. Confirm: `grep -n 'os.path.exists' oh_brother.py`
- The promote at :860 (`os.replace(part_filename, backup_path)`) is unconditional. An
  unchanged vendor artifact is re-fetched (15,270,855 bytes measured) and re-written over
  an identical retained copy.
- Because nothing records the retained copy's digest, nothing downstream can tell "the
  bytes the vendor just served are the bytes already on disk" from "the vendor's artifact
  changed".

Be precise about what a recorded digest can and cannot do, because the packet's value
depends on it. It CAN prove the retained file is bit-for-bit the one that was verified and
detect that the bytes on disk (or the vendor's artifact) have changed. It CANNOT detect a
corrupt download, because the vendor supplies no expected digest -- only the declared
length. Do not claim otherwise in code comments, docstrings, or the docs.

DO EXACTLY THIS

1. Add `SHA256_SUFFIX = '.sha256'` as a module-level constant next to BACKUP_DIRNAME
   (:125-126) -- a named constant, not an inline literal, following the module's house
   style. `hashlib` is stdlib and is NOT currently imported: add the import in the existing
   import block, keeping the sorted/existing order intact (ruff I001 flagged import order in
   this file once already -- do not reorder unrelated imports).

2. `_sha256_file(path)` -> lowercase hex digest, read in chunks. Reuse the existing
   DOWNLOAD_CHUNK constant rather than inventing a second chunk size.

3. `_sidecar_path(backup_path)` -> `backup_path + SHA256_SUFFIX`.

4. Write the sidecar at retention time. Immediately after a successful promote (:860) and
   BEFORE the `if args.test:` early return at :867 -- retention happened either way, and a
   `--test` run must leave the same state behind as a flashing run.

   Content: exactly one line, `'%s  %s\n' % (digest, os.path.basename(backup_path))` --
   two spaces, i.e. sha256sum format, so `sha256sum -c <name>.sha256` works on it from the
   containing directory. That interoperability is a requirement, not a preference; test it.

   Write it atomically: create `<sidecar>.tmp` in the same directory, then `os.replace` it
   onto the sidecar. The directory is already known to be writable (checked at :769).

   If the sidecar cannot be written: print a warning naming the path, keep the image, and
   do NOT change the exit code. A missing sidecar means "unknown", which is fail-open: the
   next run re-downloads and re-verifies. Never delete a verified image to protect a
   sidecar.

5. `_retained_copy_state(backup_path)` -> `(state, digest_or_None)`, where state is one of
   exactly these four strings:
     'absent'      no retained file (today's path)
     'verified'    file exists, sidecar parses, recorded digest == actual digest
     'unrecorded'  file exists, sidecar missing, unreadable, or malformed
     'corrupt'     file exists, sidecar parses, recorded digest != actual digest
   A malformed or unparsable sidecar is 'unrecorded', never 'corrupt': "we do not know" is
   the honest label, and it must not be promoted to a stronger claim.

   Call it once after the backup-directory check at :769 and before the fetch at :796, and
   print exactly one line reporting the state, with the first 12 hex characters of the
   digest for 'verified' and 'corrupt'. Keep it ASCII-only (Packet 1's rule: operator-facing
   output must encode as ASCII).

6. USE THE STATE FOR REPORTING AND CHANGE DETECTION ONLY. **This packet must not skip the
   download on any path.** If you find yourself writing a branch that returns without
   calling `urlopen` based on the retained copy, stop -- that is Packet 3B, and shipping it
   here without vendor corroboration would weaken the gate (the plan's gate G2: a reuse test
   must be a matching recorded hash and never size-only or exists-only, and even a matching
   hash alone is not vendor corroboration).

7. After the download passes `_verify_firmware_integrity`, and immediately before the
   promote at :859-864, compare the new file's digest with the recorded digest when -- and
   only when -- the state is 'verified':
     - identical: print that the vendor artifact is unchanged and the retained image is
       already the same bytes, and SKIP the `os.replace` (rewriting a 15 MB file with
       identical content is pure waste) and skip rewriting the sidecar. The retained file
       and its sidecar are both left exactly as they were.
     - different: print an unmissable notice -- ASCII only -- that the vendor artifact for
       this version differs from the retained copy, naming both digests in full, then
       promote as today and rewrite the sidecar to the new digest. This is the scenario
       worth a human's attention: Brother republished under an unchanged filename.

   For 'absent', 'unrecorded' and 'corrupt' the behaviour is exactly as today: promote, and
   write (or overwrite) the sidecar. A 'corrupt' retained copy must never be treated as a
   known-good source -- but it is still overwritten by the newly verified bytes; do not
   delete it pre-emptively.

8. Unchanged invariants (a violation fails the packet): promote only after the integrity
   check passes; delete the image only after a verified successful flash; `--test` never
   deletes; the partial file is never visible under a real firmware name; the promote stays
   a same-filesystem rename.

TESTS -- write them RED FIRST, and prove RED before you touch the source

Add to tests/test_oh_brother.py. For each, paste the real pre-fix failure output:
  (a) `_retained_copy_state` returns each of the four states, including a sidecar whose
      contents are garbage -> 'unrecorded' (NOT 'corrupt').
  (b) THE PLAN'S G2 CASE, and the one that must never regress: an existing retained file
      whose recorded hash is WRONG is not treated as known-good. Assert the state is
      'corrupt' AND that the download still ran (`urlopen` called exactly once).
  (c) After a successful retention, the sidecar exists, its first field equals
      hashlib.sha256 of the retained file and its second field is the basename -- and it
      parses with `sha256sum -c` semantics (assert the format explicitly; do not shell out).
  (d) Unchanged bytes: the retained file is NOT rewritten (assert its mtime and inode are
      unchanged) and no "differs" notice is printed.
  (e) Changed bytes with the same declared length: the notice names BOTH digests, the
      promote happens, and the sidecar is updated to the new digest.
  (f) A sidecar write failure leaves the exit code unchanged and the verified image on disk.
  (g) A retained file whose contents were truncated/replaced on disk while the sidecar kept
      the old digest classifies as 'corrupt'.

DIAGNOSTIC TRAPS - read before writing tests

- To prove a download did or did not happen, do NOT patch the stdlib `socket.socket` class.
  asyncio's event loop builds its self-pipe from it, so the patched version makes
  `socket.socketpair()` return mock fds and `epoll.register` dies with
  `OSError: Operation not permitted`, which the SNMP stage maps to EXIT_PRINTER (4) -- a
  plausible-looking exit code for an unrelated reason. Patch `oh.urllib.request.urlopen`
  (the module's own binding) or a SimpleNamespace onto `oh.socket`.
- Do NOT patch `hashlib.sha256` globally; patch the module's own helper if you need to.
- ALWAYS read WHY a test failed, not just that it did. A failure that names the defect (the
  state, the digest, the path) is the bar; an AttributeError on a symbol you just introduced
  means the test is new, not that it is red.

HARD CONSTRAINTS - a change outside this list fails the task

- Do NOT modify lines 1-15 of oh_brother.py (GPLv2 header). Verify afterwards:
  head -15 oh_brother.py | md5sum   must equal   37f53a239b86f53d385d45047b8ceb07
- Do NOT change any exit-code value, and do NOT add or rename any CLI flag.
- Do NOT add any dependency: stdlib + pysnmp only.
- Do NOT reformat, re-indent, reorder or "tidy" the file. update_firmware() keeps its
  upstream 2-space indentation.
- Do NOT act on any aislop finding. The swallowed `except OSError: pass` in
  `_remove_quietly`, the module globals, the module-level `parser` and the `if name == ...`
  SNMP chain are documented as accepted in the audit's section 4. A packet that changes one
  has failed.
- Create NO new files in the repository. Scratch work goes in /tmp only.
- Make no other changes anywhere. If you spot another bug, report it in your summary
  instead of fixing it.

DOCS THE CHECKER ASSERTS

Adding SHA256_SUFFIX moves the module constant count (currently 31 -- verify with
`python3 scripts/check_agents_md.py`, which prints the real count). Update
`**Module constants (N):**` in AGENTS.md, and README.md if it states the count, IN THE SAME
CHANGE, then re-run the checker until it prints "No drift". Note the checker also fails on a
backticked identifier in AGENTS.md that starts with "_" or is ALL-CAPS and does not exist in
oh_brother.py -- describe such things in words.

VERIFY AND REPORT

Run these and paste their real, unedited output in your final summary:
  python3 -m pytest tests/ -q
  python3 -m pytest tests/ -q --collect-only | tail -2
  python3 scripts/check_agents_md.py
  head -15 oh_brother.py | md5sum
  grep -n 'os.path.exists' oh_brother.py
  npx --yes aislop@latest scan --changes --json | jq '{score, summary, byKind: .findingAssessment.byKind}'
  git status --porcelain
  git diff --stat

Then prove it end-to-end against the real artifact, because this is a retention path and a
mocked test cannot show the sidecar is correct:
  OH_BROTHER_BACKUP_DIR=/tmp/p3a-live timeout 300 python3 oh_brother.py --test --reflash 192.168.88.65
and from the backup directory, `sha256sum -c <name>.djf.sha256` must print "OK". Paste the
real output of both, plus `ls -l` of the retained file and its sidecar. This is
non-destructive and retains the image; it takes about ten seconds. Report the retained
image's md5 as well (the known-good value is c5306355c75a95fa6c04109c1d7f70b0).

Leave no new untracked files behind. Finish your summary with: files changed, the exact
before/after behaviour for each test case, the full-suite wall time, and the exact commands
run.
