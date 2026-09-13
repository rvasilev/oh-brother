# Oh Brother!

A cross-platform utility written in Python which can update Brother printer
firmware. It was born out of frustration with Brother for not providing a tool
which works in Linux. It runs on any platform that has Python 3.10+.

I found information on how to do this
[here](https://cbompart.wordpress.com/2014/02/05/printer-update/) and
[here](http://pschla.blogspot.com/2013/08/resurrecting-brother-hl-2250dn-after.html).

## Credits and fork notice

This is a fork of **[oh-brother](https://github.com/CauldronDevelopmentLLC/oh-brother)
by [Joseph Coffman](https://github.com/CauldronDevelopmentLLC/cauldron)**
(upstream, 2015–2023), which is the original implementation of this idea and the
source of the SNMP discovery and firmware-upload approach. The upstream project
is the origin of this work, not a footnote to it — please go and see it. The
original copyright notice and GPLv2 licence are preserved verbatim in
[`oh_brother.py`](oh_brother.py) and [`LICENSE`](LICENSE).

This fork adds safety gates and a machine-readable exit-code contract, mostly so
the tool can refuse to do something stupid when nobody is watching (see
[Safety](#safety-and-exit-codes)).

The firmware-delivery mechanism itself (the Brother API endpoint, the filename
conventions, the raw-port upload) is entirely upstream's discovery and the
research linked above.

# Disclaimer

I hope people find this project useful but I am not getting paid for it. If you
want to support this project, please
[![donate](https://www.paypalobjects.com/en_US/i/btn/btn_donate_SM.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=J23DKKKYZRTA4).
If you think Brother should support this project
[please tell them so](https://support.brother.com/g/b/contacttop.aspx). Read the
license for the full disclaimer.

**Firmware flashing is destructive.** A failed or interrupted write can leave a
printer in a state that needs service. Read
[Safety](#safety-and-exit-codes) before running this against hardware you care
about.

# Install

The only runtime dependency is `pysnmp` (7.x, pinned below 8), which pip
installs for you. The recommended install is [pipx](https://pipx.pypa.io/):

```
pipx install git+https://github.com/rvasilev/oh-brother
```

or with pip:

```
pip install git+https://github.com/rvasilev/oh-brother
```

Either way you get an `oh-brother` command on your PATH:

```
oh-brother --version
oh-brother --help
```

From a git checkout you can also skip installation entirely and run the module
directly, which still needs `pysnmp` importable:

```
pip install -r requirements.txt
./oh_brother.py <ip address of printer>
```

> **If you are reading an older copy of these instructions:** they used to say
> `apt-get install python3-pysnmp4` or `yum install python-pysnmp`. Those
> packages ship pysnmp **4.x**, which does not contain the `pysnmp.hlapi.v1arch`
> API this tool was migrated to, and produces an immediate `ImportError`. Do not
> install pysnmp from the distribution packages; let pip resolve it.

# What it does

Currently the script does the following:

  * Query the printer's information via the SNMP protocol.
  * Print SNMP info to screen.
  * For each firmware type:
    * Query Brother servers for the latest firmware.
    * Refuse to continue if the printer already reports that version and
      `--reflash` was not given.
    * Download the firmware from Brother, verifying its size against the
      declared `Content-Length`.
    * Retain the downloaded image under
      `firmware_backups/<MODEL>/<version>/` as a recovery copy.
    * Ask the user whether to proceed with updating (or, with `--yes`, proceed).
    * Upload the firmware to the printer via either TCP port 9100
      (passwordless) or FTP (with admin password).
    * Poll the printer back up and confirm it is running the expected version.
    * Delete the retained image only after that verification passes.

# Safety and exit codes

The tool is designed to be safe to run unattended, which means it refuses
anything ambiguous rather than guessing. In particular it will **not**:

  * flash firmware when the printer already reports the current version, unless
    you explicitly pass `--reflash`;
  * flash a downloaded file whose version is *older* than what is installed —
    there is no override flag for this;
  * flash at all when stdin is not a terminal and `--yes` was not given, so a
    cron job cannot silently reflash a printer;
  * download from a host that merely *ends with* an allow-listed domain name.

Exit codes are a stable contract for scripting:

| Code | Meaning |
|------|---------|
| 0 | OK — firmware uploaded and verified, or `--test` fetched and verified an image |
| 1 | ERROR — unexpected internal error |
| 2 | USAGE — bad arguments |
| 3 | CURRENT — nothing to do, printer already current (a healthy cron no-op) |
| 4 | PRINTER — printer unreachable |
| 5 | VENDOR — Brother API error, or no firmware URL |
| 6 | DOWNLOAD — download or integrity check failed |
| 7 | UPLOAD — upload failed, printer rejected the image, or the version did not match |
| 8 | UNVERIFIED — uploaded, but the printer did not come back in time to confirm |
| 9 | REFUSED — a safety gate declined to proceed |

Exit code **8 is deliberately not a failure**: a Brother laser reboots for a
minute or two after a flash, and reporting a false failure there is how people
end up reflashing a printer that is already working. Check the printer before
reacting to an 8. Anything at 7 or 9 needs a human.

If a transfer is interrupted mid-write the tool keeps the image and prints
`TRANSFER INCOMPLETE — DO NOT POWER OFF; reflash from <path>`. Heed it. The
retained image under `firmware_backups/` is what you retry from.

# How to use it

You need to know both the IP address of your printer, and the *admin* password
if uploading firmware via FTP. Run the script and press ```Enter``` after each
firmware has completed updating.

```
oh-brother <ip address of printer>
```

## Check what a firmware update would do, without flashing

`--test` downloads, verifies and retains the image but never uploads. It needs
`--reflash` if the printer already reports the current version, because
otherwise there is nothing to fetch:

```
oh-brother --test --reflash <printer IP>
```

That is the safe way to obtain a local backup of the firmware your printer is
currently running. The image is left at
`firmware_backups/<MODEL>/<version>/<firmware file>`.

## Re-apply the current version

Useful when recovering from a botched update — this is the reflash the
`TRANSFER INCOMPLETE` message is telling you to perform:

```
oh-brother --reflash <printer IP>
```

## Unattended use

Add `--yes`. Without it, a run with no terminal is refused (exit 9) rather than
flashed. Pair it with the exit codes above:

```
oh-brother --reflash --yes <printer IP>
case $? in
  0) echo "flashed and verified" ;;
  3) echo "already current" ;;
  *) echo "needs attention" >&2 ;;
esac
```

## If it doesn't work for you

YMMV.

In order to send firmware updates via TCP port 9100, "Raw Port" must be
enabled in the printer's management interface. In order to send firmware
updates via FTP, you must have first set an admin password on the
printer via the web interface.

## Use ``--category``

Try specifying ``--category`` on the command line.  E.g.:

    oh-brother --category MAIN <printer IP>

This targets a specific firmware category. Note that forcing a category no
longer lets you push an *older* image: a downgrade is refused regardless, and
category selection on its own will not bypass the version check.

## Submit a PR

Please feel free to submit a pull-request — including to upstream, which is the
natural home for anything that is not specific to this fork.

## Other options

An alternate bash script for firmware download can be found
[here](https://cbompart.wordpress.com/2014/05/26/brother-printer-firmware-part-2/).

## AI disclosure

This project is developed with AI assistance. This section documents how, so
users and downstream packagers can make informed decisions.

**Tools:** Hermes Agent (DeepSeek v4.1-flash), Crush CLI — invoked locally with
project-scoped rules and memory.

**Used for:** Refactors, multi-file edits, boilerplate (error enums, test
scaffolding, doc polish), pysnmp 4.x→7.x migration patterns, exploratory design
conversations, and adversarial audit passes whose findings were independently
reproduced before being acted on.

**Not used for:** Engineering decisions, firmware protocol analysis
(SNMP/XML/raw-port), git manipulation, real-printer integration tests.

**Verification:** Every AI-assisted change is read, compiled, tested
(`python3 -m pytest tests/ -q`, 91 tests), and reviewed as a diff before commit.
Where AI proposed a fix, the test proving it was run against the *unfixed* source
first to confirm it actually fails — a test that passes both before and after
proves nothing. Behavioural correctness is verified against real printer SNMP
data and Brother API responses, not assumed from model output. Tests are never
adjusted to fit AI-generated code; the code is adjusted to fit correct
behaviour.

**Limitations:** AI models occasionally produce code that compiles and passes
tests but is subtly wrong — particularly around async pysnmp 7.x semantics and
TCP `sendfile` edge cases. The verification workflow catches most of this; it
does not catch all of it. Bug reports are welcome and taken seriously.

**Last reviewed:** 13/09/2026
