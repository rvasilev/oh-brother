#!/usr/bin/env python3
"""Fail if AGENTS.md or README.md has drifted from the source.

Documentation rots silently: a stale Known-issues table or an out-of-date test
count sends the next reader to work that is already done. This script turns
that into a checkable claim.

Run:  python3 scripts/check_agents_md.py
Exit: 0 = no drift, 1 = drift found (print a report)

Deliberately stdlib-only and independent of the test suite, so it can run
anywhere the repo is checked out. It is NOT wired into CI — see AGENTS.md.
"""
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "oh_brother.py"
TESTS = ROOT / "tests" / "test_oh_brother.py"
AGENTS = ROOT / "AGENTS.md"
README = ROOT / "README.md"
PYPROJECT = ROOT / "pyproject.toml"

# Tokens that are standard-library, wire-protocol or vendor-quirk names rather
# than identifiers belonging to this project.
EXTERNAL = {
    "input", "open", "print", "int", "str", "len", "any", "getattr", "replace",
    "sendfile", "QUIT", "STOR", "SELIALNO", "os", "re", "sys", "time", "socket",
    "urllib", "ftplib", "argparse", "asyncio", "traceback", "importlib",
    "tomllib", "quote",
    # Uppercase prose: message fragments and vendor wire values.
    "DO", "TRANSFER", "FAILURE", "GPL", "VERSIONCHECK", "EXIT_",
}

# Phrases that must exist in the source for the documented gates to be real.
# Each sits inside ONE string literal: the source splits long messages across
# adjacent literals, so a phrase spanning two of them never matches.
GATE_PHRASES = [
    ("requires -c/--category", "-f requires -c"),
    ("REFUSING: --beta", "--beta needs --yes"),
    ("REFUSING to flash firmware unattended", "non-TTY refusal"),
    ("REFUSING to flash: artifact version", "downgrade refusal"),
    ("was given but the printer does not", "-c with unknown category refused"),
]

STALE_PHRASES = [
    "30s delay",
    "update loop with cooldown",
    "3.10 compatible but untested",
    "downloads then deletes",
]


def main() -> int:
    src = SOURCE.read_text()
    tests = TESTS.read_text()
    agents = AGENTS.read_text()
    readme = README.read_text()
    pyproject = tomllib.loads(PYPROJECT.read_text())

    funcs = set(re.findall(r"^(?:async )?def (\w+)", src, re.M))
    klasses = set(re.findall(r"^class (\w+)", src, re.M))
    # Assignments are space-aligned (`EXIT_CURRENT    = 3`), so `\s*=` is
    # required; `name = ` silently misses most of the constants.
    consts = set(re.findall(r"^([A-Z][A-Z0-9_]+)\s*=", src, re.M))
    known = funcs | klasses | consts
    codes = dict(re.findall(r"^(EXIT_\w+)\s*=\s*(\d+)", src, re.M))

    problems: list[str] = []

    # 1. identifiers referenced in AGENTS.md must exist
    refs = set(re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]*)\(?", agents))
    missing = sorted(r for r in refs - known - EXTERNAL
                     if r.startswith("_") or r.isupper())
    if missing:
        problems.append(f"AGENTS.md references undefined identifiers: {missing}")

    # 2. line count
    m = re.search(r"\*\*Lines:\*\* ~([\d,]+)", agents)
    actual_lines = len(src.splitlines())
    if m and abs(int(m.group(1).replace(",", "")) - actual_lines) > 60:
        problems.append(f"line claim ~{m.group(1)} vs actual {actual_lines}")

    # 3. GPL header hash — attribution must survive refactors
    m = re.search(r"`([0-9a-f]{32})`", agents)
    if not m:
        problems.append("no GPL header md5 recorded in AGENTS.md")
    else:
        got = subprocess.run(
            ["bash", "-c", f"head -15 {SOURCE} | md5sum | cut -d' ' -f1"],
            capture_output=True, text=True).stdout.strip()
        if got != m.group(1):
            problems.append(f"header md5 claim {m.group(1)} != actual {got}")

    # 4. version
    m = re.search(r"\*\*Version:\*\* `([\d.]+)`", agents)
    declared = pyproject["project"]["version"]
    if m and m.group(1) != declared:
        problems.append(f"version claim {m.group(1)} != pyproject {declared}")

    # 5. constant count
    m = re.search(r"\*\*Module constants \((\d+)\):\*\*", agents)
    if m and int(m.group(1)) != len(consts):
        problems.append(f"constant count claim {m.group(1)} != actual {len(consts)}")

    # 6. CLI flags: every argparse flag documented, nothing invented.
    #
    # argparse declares short and long forms as separate arguments
    # (add_argument('-t', '--test')), so parsing only the first string literal
    # misses every long form and the check silently passes on nothing.
    parser_flags: set[str] = set()
    for seg in src.split("add_argument(")[1:]:
        segment = seg.split(")")[0]
        parser_flags |= set(re.findall(r"'(--?[a-zA-Z][\w-]*)'", segment))

    cli = agents[agents.find("## CLI reference"):agents.find("## Flashing gates")]
    listed: set[str] = set()
    for line in cli.splitlines():
        # Anchor to the leading flag field (`-t, --test`), so dashes inside the
        # help text ("Re-apply", "non-interactive") are not read as flags.
        m = re.match(
            r"\s+((?:--?[a-zA-Z][\w-]*)(?:\s*,\s*--?[a-zA-Z][\w-]*)*)", line)
        if m:
            listed |= set(re.findall(r"--?[a-zA-Z][\w-]*", m.group(1)))

    invented = sorted(listed - parser_flags)
    if invented:
        problems.append(f"CLI block lists flags not in argparse: {invented}")
    undocumented = sorted(f for f in parser_flags
                          if f not in listed and f not in {"-h", "--help"})
    if undocumented:
        problems.append(f"argparse flags missing from CLI block: {undocumented}")

    # 7. exit codes agree in both documents
    for doc, text in (("AGENTS.md", agents), ("README.md", readme)):
        for num, name in re.findall(r"\|\s*(\d+)\s*\|\s*`(EXIT_\w+)`", text):
            if codes.get(name) != num:
                problems.append(
                    f"{doc} says {name}={num}, source says {name}={codes.get(name)}")
    doc_codes = set(re.findall(r"^\|\s*(\d+)\s*\|", agents, re.M))
    doc_codes |= set(re.findall(r"^\|\s*(\d+)\s*\|", readme, re.M))
    missing_codes = sorted(set(codes.values()) - doc_codes, key=int)
    if missing_codes:
        problems.append(f"exit codes absent from the documented tables: {missing_codes}")

    # 8. documented gates are real
    for phrase, label in GATE_PHRASES:
        if phrase not in src:
            problems.append(f"gate '{label}' documented but absent from source")

    # 9. test counts and the per-class table
    out = subprocess.run([sys.executable, "-m", "pytest", "tests/",
                          "--collect-only", "-q"],
                         capture_output=True, text=True, cwd=ROOT).stdout
    collected = [ln for ln in out.splitlines() if "::" in ln]
    perclass: dict[str, int] = {}
    for ln in collected:
        cls = ln.split("::")[1]
        perclass[cls] = perclass.get(cls, 0) + 1
    n_tests = len(collected)
    n_classes = len(set(re.findall(r"^class (\w+)", tests, re.M)))
    m = re.search(r"\*\*(\d+) tests, (\d+) classes", agents)
    if m:
        if int(m.group(1)) != n_tests:
            problems.append(f"test count claim {m.group(1)} != collected {n_tests}")
        if int(m.group(2)) != n_classes:
            problems.append(f"class count claim {m.group(2)} != actual {n_classes}")
    for cls, claimed in re.findall(r"\|\s*`(\w+)`\s*\|\s*(\d+)", agents):
        if cls in perclass and int(claimed) != perclass[cls]:
            problems.append(f"test table says {cls}={claimed}, actual {perclass[cls]}")
    table = set(re.findall(r"\|\s*`(Test\w+)`", agents))
    if table != set(perclass):
        problems.append(
            f"test table class set differs: missing {sorted(set(perclass) - table)}, "
            f"extra {sorted(table - set(perclass))}")

    # 10. interpreter floor and dependency pin
    if pyproject["project"]["requires-python"] != ">=3.10":
        problems.append(f"requires-python changed: {pyproject['project']['requires-python']}")
    if not re.search(r"requires-python = \">=3\.10\"|Python 3\.10\+", agents):
        problems.append("AGENTS.md does not state the 3.10 floor")
    if not any(d.startswith("pysnmp>=7.1.0,<8") for d in pyproject["project"]["dependencies"]):
        problems.append(f"pysnmp pin changed: {pyproject['project']['dependencies']}")

    # 11. stale claims that have been superseded by behaviour
    for bad in STALE_PHRASES:
        if bad in agents:
            problems.append(f"stale phrase still present in AGENTS.md: {bad!r}")

    print(f"constants: {len(consts)}  functions: {len(funcs)}  "
          f"tests: {n_tests}  classes: {n_classes}  lines: {actual_lines}")
    if problems:
        print(f"\nDRIFT ({len(problems)}):")
        for p in problems:
            print("  -", p)
        return 1
    print("No drift: every documented claim matches the source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
