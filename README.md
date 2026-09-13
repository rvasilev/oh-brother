# Oh Brother!
Oh Brother! is a simple cross-platform utility written in Python which can
update Brother printer firmwares.  It was born out of frustration with Brother
for not providing a tool which works in Linux.  This tool should work on any
platform that has Python 3 with ``python-pysnmp4`` and ``python-pyasn1``.

I found information on how to do this
[here](https://cbompart.wordpress.com/2014/02/05/printer-update/) and
[here](http://pschla.blogspot.com/2013/08/resurrecting-brother-hl-2250dn-after.html).

# Disclaimer
I hope people find this project useful but I am not getting paid for it.  If you want to support this project, please [![donate](https://www.paypalobjects.com/en_US/i/btn/btn_donate_SM.gif)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=J23DKKKYZRTA4).  If you think Brother should support this project [please tell them so](https://support.brother.com/g/b/contacttop.aspx).  Read the license for the full disclaimer.

# Install prerequisites

The `requirements.txt` file contains the required dependency versioning for
the PySNMP dependency. It can be installed with:

```
pip install -r requirements.txt
```

## On Debian-based distributions (e.g. Debian/Mint/Ubuntu)

```
sudo apt-get install -y python3-pysnmp4
```

## On RedHat-based distributions (e.g. Fedora/CentOS)

```
sudo yum install python-pysnmp
```

## On OSX or Windows with Python installer (PIP)

```
pip install pysnmp
```

# What it does
Currently the script does the following:

  * Query the printer's information via the SNMP protocol.
  * Print SNMP info to screen.
  * For each firmware type:
    * Query Brother servers for the latest firmware.
    * Download the firmware from Brother.
    * Display firmware info, then ask user whether to proceed with updating.
    * Upload the firmware to the printer via either TCP port 9100 (passwordless) or FTP (with admin password).
    * Wait for user to signal that the update is done.

# How to use it
You need to know both the IP address of your printer, and the *admin* password
if uploading firmware via FTP.
Run the script and press ```Enter``` after each
firmware has completed updating.


```
./oh-brother.py <ip address of printer>
```

# If it doesn't work for you
YMMV.

In order to send firmware updates via TCP port 9100, "Raw Port" must be
enabled in the printer's management interface. In order to send firmware
updates via FTP, you must have first set an admin password on the
printer via the web interface.

## Use ``--category``
Try specifying ``--category`` on the command line.  E.g.:

    ./oh-brother.py --category MAIN <printer IP>

This will force the script to update a specific firmware regardless of the
version you currently have.

## Submit a PR
Please feel free to submit a pull-request.

## Other options
An alternate bash script for firmware download can be found
[here](https://cbompart.wordpress.com/2014/05/26/brother-printer-firmware-part-2/).

## AI disclosure

This project is developed with AI assistance. This section documents how, so users and downstream packagers can make informed decisions.

**Tools:** Hermes Agent (DeepSeek v4-pro), Crush CLI — invoked locally with project-scoped rules and memory.

**Used for:** Refactors, multi-file edits, boilerplate (error enums, test scaffolding, doc polish), pysnmp 4.x→7.x migration patterns, exploratory design conversations.

**Not used for:** Engineering decisions, firmware protocol analysis (SNMP/XML/raw-port), git manipulation, real-printer integration tests.

**Verification:** Every AI-assisted change is read, compiled, tested (`python3 -m pytest tests/ -v`, 74 tests), and formatted before commit. Behavioural correctness is verified against real printer SNMP data and Brother API responses, not assumed from model output. Tests are never adjusted to fit AI-generated code; the code is adjusted to fit correct behaviour.

**Limitations:** AI models occasionally produce code that compiles and passes tests but is subtly wrong — particularly around async pysnmp 7.x semantics and TCP sendfile edge cases. The verification workflow catches most of this; it does not catch all of it. Bug reports are welcome and taken seriously.

**Last reviewed:** 30/07/2026
