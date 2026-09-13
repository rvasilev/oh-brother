import importlib.util
import os
import sys
import xml.etree.ElementTree as ET
import pytest

# Import oh_brother.py by path
module_path = os.path.join(os.path.dirname(__file__), '..', 'oh_brother.py')
spec = importlib.util.spec_from_file_location("oh_brother", module_path)
oh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oh)


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

REAL_SNMP_TABLE = [
    [("1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.1", 'MODEL="HL-L2865DW"')],
    [("1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.2", 'SERIAL="U00000A0A000000"')],
    [("1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.3", 'SPEC="0906"')],
    [("1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.4", 'DEMOID="?"')],
    [("1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.5", 'FONT="?"')],
    [("1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.6", 'FIRMID="MAIN"')],
    [("1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.7", 'FIRMVER="1.24"')],
]

REAL_BROTHER_RESPONSE_UP_TO_DATE = (
    b'<?xml version="1.0" encoding="UTF-8" ?>'
    b'<RESPONSEINFO>'
    b'<FIRMUPDATEINFO>'
    b'<VERSIONCHECK>1</VERSIONCHECK>'
    b'<FIRMID>MAIN</FIRMID>'
    b'</FIRMUPDATEINFO>'
    b'</RESPONSEINFO>'
)


# ---------------------------------------------------------------------------
# parse_snmp_table
# ---------------------------------------------------------------------------

class TestParseSnmpTable:
    def test_real_printer_data(self):
        """Full extraction from real HL-L2865DW SNMP output."""
        result = oh.parse_snmp_table(REAL_SNMP_TABLE)
        assert result['serial'] == 'U00000A0A000000'
        assert result['model'] == 'HL-L2865DW'
        assert result['spec'] == '0906'
        assert len(result['firmwares']) == 1
        assert result['firmwares'][0] == {'cat': 'MAIN', 'version': '1.24'}

    def test_multiple_firmwares(self):
        """Printer with MAIN + SUB1 firmware."""
        table = [
            [("...6", 'FIRMID="MAIN"')],
            [("...7", 'FIRMVER="1.24"')],
            [("...6", 'FIRMID="SUB1"')],
            [("...7", 'FIRMVER="2.10"')],
        ]
        result = oh.parse_snmp_table(table)
        assert result['firmwares'] == [
            {'cat': 'MAIN', 'version': '1.24'},
            {'cat': 'SUB1', 'version': '2.10'},
        ]

    def test_firmver_before_firmid(self):
        """FIRMVER appearing before its FIRMID — should be skipped."""
        table = [
            [("...7", 'FIRMVER="1.24"')],  # No FIRMID yet — skip
            [("...6", 'FIRMID="MAIN"')],
            [("...7", 'FIRMVER="1.25"')],  # Now paired with MAIN
        ]
        result = oh.parse_snmp_table(table)
        assert len(result['firmwares']) == 1
        assert result['firmwares'][0] == {'cat': 'MAIN', 'version': '1.25'}

    def test_no_equals_sign_skipped(self):
        """Rows without '=' should be silently ignored."""
        table = [
            [("...x", "0x0c")],  # Raw hex byte, no '='
            [("...6", 'FIRMID="MAIN"')],
            [("...7", 'FIRMVER="1.24"')],
        ]
        result = oh.parse_snmp_table(table)
        assert result['model'] is None
        assert result['firmwares'][0] == {'cat': 'MAIN', 'version': '1.24'}

    def test_empty_table(self):
        """Empty table returns all None/empty."""
        result = oh.parse_snmp_table([])
        assert result['serial'] is None
        assert result['model'] is None
        assert result['spec'] is None
        assert result['firmwares'] == []

    def test_model_only(self):
        """Table with only MODEL — no serial, no firmware."""
        table = [[("...1", 'MODEL="HL-L2865DW"')]]
        result = oh.parse_snmp_table(table)
        assert result['model'] == 'HL-L2865DW'
        assert result['serial'] is None
        assert result['firmwares'] == []

    def test_verbose_output(self, capsys):
        """Verbose mode prints the table."""
        oh.parse_snmp_table(REAL_SNMP_TABLE, verbose=True)
        captured = capsys.readouterr()
        assert 'HL-L2865DW' in captured.out


# ---------------------------------------------------------------------------
# build_firmware_xml
# ---------------------------------------------------------------------------

class TestBuildFirmwareXml:
    def test_basic_xml_structure(self):
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'MAIN', '1.24')
        root = ET.fromstring(xml_bytes)
        assert root.find('FIRMUPDATETOOLINFO/FIRMCATEGORY').text == 'MAIN'
        assert root.find('FIRMUPDATETOOLINFO/OS').text == 'WIN_NATIVE'
        assert root.find('FIRMUPDATETOOLINFO/INSPECTMODE').text == '0'
        assert root.find('FIRMUPDATEINFO/MODELINFO/NAME').text == 'HL-L2865DW'
        assert root.find('FIRMUPDATEINFO/MODELINFO/SPEC').text == '0906'
        firm = root.find('FIRMUPDATEINFO/MODELINFO/FIRMINFO/FIRM')
        assert firm.find('ID').text == 'MAIN'
        assert firm.find('VERSION').text == '1.24'

    def test_firm_category_mapped_to_main(self):
        """FIRM category should be mapped to MAIN in FIRMCATEGORY element."""
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'FIRM', '1.00')
        root = ET.fromstring(xml_bytes)
        assert root.find('FIRMUPDATETOOLINFO/FIRMCATEGORY').text == 'MAIN'

    def test_ifax_id_mapped_to_main(self):
        """IFAX firm ID should be mapped to MAIN in ID element."""
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'IFAX', '2.00')
        root = ET.fromstring(xml_bytes)
        firm = root.find('FIRMUPDATEINFO/MODELINFO/FIRMINFO/FIRM')
        assert firm.find('ID').text == 'MAIN'

    def test_beta_mode(self):
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'MAIN', '1.24',
                                          beta=True)
        root = ET.fromstring(xml_bytes)
        assert root.find('FIRMUPDATETOOLINFO/INSPECTMODE').text == '1'

    def test_output_is_bytes(self):
        result = oh.build_firmware_xml('HL-L2865DW', '0906', 'MAIN', '1.24')
        assert isinstance(result, bytes)

    def test_driver_is_ews(self):
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'MAIN', '1.24')
        root = ET.fromstring(xml_bytes)
        assert root.find('FIRMUPDATEINFO/MODELINFO/DRIVER').text == 'EWS'


# ---------------------------------------------------------------------------
# parse_brother_response
# ---------------------------------------------------------------------------

class TestParseBrotherResponse:
    def test_up_to_date(self):
        result = oh.parse_brother_response(REAL_BROTHER_RESPONSE_UP_TO_DATE)
        assert result['version_check'] == '1'
        assert result['firmware_url'] is None

    def test_update_available(self):
        xml = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<PATH>http://update-akamai.brother.co.jp/CS/D00XXX_A.djf</PATH>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )
        result = oh.parse_brother_response(xml)
        assert result['version_check'] == '0'
        assert result['firmware_url'] == (
            'http://update-akamai.brother.co.jp/CS/D00XXX_A.djf'
        )

    def test_no_path_element(self):
        """Newer models (HL-L2865DW) may return no PATH — should return None."""
        xml = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<FIRMID>MAIN</FIRMID>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )
        result = oh.parse_brother_response(xml)
        assert result['version_check'] == '0'
        assert result['firmware_url'] is None

    def test_no_versioncheck(self):
        """Response missing VERSIONCHECK entirely."""
        xml = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<PATH>http://example.com/firmware.djf</PATH>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )
        result = oh.parse_brother_response(xml)
        assert result['version_check'] is None
        assert result['firmware_url'] is not None

    def test_empty_response(self):
        """Totally unexpected XML — shouldn't crash."""
        xml = b'<?xml version="1.0" encoding="UTF-8" ?><OTHER/>'
        result = oh.parse_brother_response(xml)
        assert result['version_check'] is None
        assert result['firmware_url'] is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    """CLI argument parsing — parameterized to avoid testing stdlib argparse."""

    @pytest.mark.parametrize("args_str,attr,expected", [
        # Boolean flags (default False)
        ("1.2.3.4", "yes", False),
        ("1.2.3.4", "test", False),
        ("1.2.3.4", "verbose", False),
        ("1.2.3.4", "beta", False),
        # Boolean flags (set)
        ("--yes 1.2.3.4", "yes", True),
        ("--test 1.2.3.4", "test", True),
        ("--verbose 1.2.3.4", "verbose", True),
        ("--beta 1.2.3.4", "beta", True),
    ])
    def test_boolean_flags(self, args_str, attr, expected):
        args = oh.parser.parse_args(args_str.split())
        assert getattr(args, attr) == expected

    @pytest.mark.parametrize("args_str,attr,expected", [
        # String args (defaults)
        ("1.2.3.4", "community", "public"),
        ("1.2.3.4", "fw_version", "B0000000000"),
        ("1.2.3.4", "model", None),
        ("1.2.3.4", "category", None),
        ("1.2.3.4", "password", None),
        # String args (set)
        ("--community private 1.2.3.4", "community", "private"),
        ("--model HL-1110 1.2.3.4", "model", "HL-1110"),
        ("--password admin123 1.2.3.4", "password", "admin123"),
    ])
    def test_string_args(self, args_str, attr, expected):
        args = oh.parser.parse_args(args_str.split())
        assert getattr(args, attr) == expected

    def test_category_with_version(self):
        args = oh.parser.parse_args(
            "--category SUB1 --fw-version 2.00 1.2.3.4".split())
        assert args.category == "SUB1"
        assert args.fw_version == "2.00"

    def test_ip_required(self):
        with pytest.raises(SystemExit):
            oh.parser.parse_args([])

    def test_parser_accessible(self):
        """Module-level parser is importable."""
        assert hasattr(oh, "parser")


# ---------------------------------------------------------------------------
# Global state reset fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_global_state(monkeypatch):
    """Reset oh module globals between tests to prevent cross-test leakage."""
    for attr in ("args", "model", "spec", "serial", "firmInfo"):
        monkeypatch.setattr(oh, attr, None, raising=False)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

class TestMainSmoke:
    """Smoke tests for main() — verifies orchestration without real I/O."""

    def test_main_parses_snmp_and_calls_update(self, monkeypatch):
        """main() extracts SNMP data then calls update_firmware for each category."""
        from unittest.mock import MagicMock

        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in REAL_SNMP_TABLE:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr("builtins.input", lambda _=None: None)

        called_with = []
        def fake_update(cat, ver):
            called_with.append((cat, ver))
            return oh.EXIT_OK
        monkeypatch.setattr(oh, "update_firmware", fake_update)

        monkeypatch.setattr(oh, "_snmp_walk_table", fake_walk_cmd)
        monkeypatch.setattr("sys.argv", ["oh-brother.py", "1.2.3.4"])
        assert oh.main() == oh.EXIT_OK

        assert called_with == [("MAIN", "1.24")]

    def test_main_model_override(self, monkeypatch):
        """--model flag overrides SNMP-discovered model."""
        from unittest.mock import MagicMock

        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in REAL_SNMP_TABLE:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr("builtins.input", lambda _=None: None)
        called_with = []
        monkeypatch.setattr(
            oh, "update_firmware",
            lambda c, v: called_with.append((c, v)) or oh.EXIT_OK,
        )

        monkeypatch.setattr(oh, "_snmp_walk_table", fake_walk_cmd)
        monkeypatch.setattr(
            "sys.argv",
            ["oh-brother.py", "--model", "HL-9999", "1.2.3.4"],
        )
        oh.main()

        assert called_with == [("MAIN", "1.24")]

    def test_main_category_override(self, monkeypatch):
        """--category + --version replace all firmware entries."""
        from unittest.mock import MagicMock

        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in REAL_SNMP_TABLE:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr("builtins.input", lambda _=None: None)
        called_with = []
        monkeypatch.setattr(
            oh, "update_firmware",
            lambda c, v: called_with.append((c, v)) or oh.EXIT_OK,
        )

        monkeypatch.setattr(oh, "_snmp_walk_table", fake_walk_cmd)
        monkeypatch.setattr(
            "sys.argv",
            ["oh-brother.py", "--category", "SUB1", "--fw-version", "3.00", "1.2.3.4"],
        )
        oh.main()

        assert called_with == [("SUB1", "3.00")]

    def test_main_snmp_error_raises(self, monkeypatch):
        """SNMP error raises Exception."""
        from unittest.mock import MagicMock

        async def fake_walk_cmd(*args, **kwargs):
            print("SNMP timeout", file=sys.stderr)
            sys.exit(1)

        monkeypatch.setattr("builtins.input", lambda _=None: None)

        monkeypatch.setattr(oh, "_snmp_walk_table", fake_walk_cmd)
        monkeypatch.setattr("sys.argv", ["oh-brother.py", "1.2.3.4"])
        with pytest.raises(SystemExit):
            oh.main()

    def test_main_snmp_status_raises(self, monkeypatch):
        """SNMP non-zero status raises Exception."""
        from unittest.mock import MagicMock

        mock_status = MagicMock()
        async def fake_walk_cmd(*args, **kwargs):
            print('ERROR: %s at %s' % (
                mock_status.prettyPrint(), '?.1.2.3'),
                file=sys.stderr)
            sys.exit(1)

        monkeypatch.setattr("builtins.input", lambda _=None: None)

        monkeypatch.setattr(oh, "_snmp_walk_table", fake_walk_cmd)
        monkeypatch.setattr("sys.argv", ["oh-brother.py", "1.2.3.4"])
        with pytest.raises(SystemExit):
            oh.main()

    def test_main_multiple_firmwares_with_delay(self, monkeypatch):
        """Multiple firmwares — time.sleep called between updates."""
        from unittest.mock import MagicMock

        # SNMP data with MAIN + SUB1 firmware
        multi_fw_table = [
            [("...1", 'MODEL="HL-L2865DW"')],
            [("...2", 'SPEC="0906"')],
            [("...6", 'FIRMID="MAIN"')],
            [("...7", 'FIRMVER="1.24"')],
            [("...6", 'FIRMID="SUB1"')],
            [("...7", 'FIRMVER="2.10"')],
        ]

        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in multi_fw_table:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr("builtins.input", lambda _=None: None)
        monkeypatch.setattr(oh, "_snmp_walk_table", fake_walk_cmd)
        monkeypatch.setattr("sys.argv", ["oh-brother.py", "1.2.3.4"])

        called_with = []
        def fake_update(cat, ver):
            called_with.append((cat, ver))
            return oh.EXIT_OK
        monkeypatch.setattr(oh, "update_firmware", fake_update)

        sleep_calls = []
        monkeypatch.setattr(oh.time, "sleep", lambda s: sleep_calls.append(s))

        oh.main()

        assert called_with == [("MAIN", "1.24"), ("SUB1", "2.10")]
        # Delay should be inserted between the two updates
        assert len(sleep_calls) >= 1


# ---------------------------------------------------------------------------
# update_firmware()
# ---------------------------------------------------------------------------

class TestUpdateFirmware:
    """Tests for update_firmware() with mocked external I/O."""

    def test_version_up_to_date(self, monkeypatch):
        """VERSIONCHECK=1 without --reflash → EXIT_CURRENT, no fallback."""
        from unittest.mock import MagicMock
        from types import SimpleNamespace

        oh.args = SimpleNamespace(
            beta=False, verbose=False, test=False, yes=False,
            ip="1.2.3.4", password=None, reflash=False,
        )
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        mock_response = MagicMock()
        mock_response.read.return_value = REAL_BROTHER_RESPONSE_UP_TO_DATE

        monkeypatch.setattr(oh.urllib.request, "urlopen", lambda req, timeout=None: mock_response)

        result = oh.update_firmware("MAIN", "1.24")
        assert result == oh.EXIT_CURRENT
        assert result != oh.EXIT_OK

    def test_no_path_returns_none(self, monkeypatch):
        """No PATH element and fallback fails → EXIT_VENDOR."""
        from unittest.mock import MagicMock
        from types import SimpleNamespace

        oh.args = SimpleNamespace(
            beta=False, verbose=False, test=False, yes=False,
            ip="1.2.3.4", password=None, reflash=False,
        )
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        xml_no_path = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<FIRMID>MAIN</FIRMID>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )

        mock_response = MagicMock()
        mock_response.read.return_value = xml_no_path

        monkeypatch.setattr(oh.urllib.request, "urlopen", lambda req, timeout=None: mock_response)

        result = oh.update_firmware("MAIN", "1.24")
        assert result == oh.EXIT_VENDOR

    def test_test_flag_stops_before_upload(self, monkeypatch, tmp_path):
        """--test downloads firmware but does not upload; image is retained."""
        from unittest.mock import MagicMock
        from types import SimpleNamespace

        oh.args = SimpleNamespace(
            beta=False, verbose=False, test=True, yes=False,
            ip="1.2.3.4", password=None, reflash=False,
        )
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        xml_update = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<PATH>http://update-akamai.brother.co.jp/CS/D00XXX_A.djf</PATH>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )

        body = b"F" * 200000

        def fake_urlopen(req, timeout=None):
            m = MagicMock()
            m.headers = {'Content-Length': str(len(body))}
            m.read.side_effect = [body, b'']
            return m

        def fail_socket(*args, **kwargs):
            raise AssertionError("no socket may be created in --test mode")

        monkeypatch.setattr(
            oh, '_http_post',
            lambda url, data, hdrs, timeout=30: (xml_update, None))
        monkeypatch.setattr(oh.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(oh.socket, "getaddrinfo", fail_socket)
        monkeypatch.setattr(oh.socket, "socket", fail_socket)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware("MAIN", "1.24")

        assert result == oh.EXIT_OK
        retained = list((tmp_path / oh.BACKUP_DIRNAME).rglob("*.djf"))
        assert len(retained) == 1
        assert retained[0].stat().st_size == len(body)
        assert not list(tmp_path.rglob("*.part"))

    def test_yes_skips_prompts(self, monkeypatch):
        """--yes flag skips all input() prompts."""
        from unittest.mock import MagicMock
        from types import SimpleNamespace

        oh.args = SimpleNamespace(
            beta=False, verbose=False, test=False, yes=True,
            ip="1.2.3.4", password=None, reflash=False,
        )
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        mock_response = MagicMock()
        mock_response.read.return_value = REAL_BROTHER_RESPONSE_UP_TO_DATE

        input_calls = []
        monkeypatch.setattr("builtins.input", lambda _=None: input_calls.append(1) or "")
        monkeypatch.setattr(oh.urllib.request, "urlopen", lambda req, timeout=None: mock_response)

        oh.update_firmware("MAIN", "1.24")
        assert len(input_calls) == 0

    def test_vcheck1_fallback_succeeds(self, monkeypatch, tmp_path):
        """VCHECK=1 WITH --reflash → retries with decremented version, gets PATH."""
        from unittest.mock import MagicMock
        from types import SimpleNamespace

        oh.args = SimpleNamespace(
            beta=False, verbose=False, test=True, yes=True,
            ip="1.2.3.4", password=None, reflash=True,
        )
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        xml_update = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<PATH>http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf</PATH>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )

        call_count = [0]

        def mock_urlopen(req, timeout=None):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                # First call: up to date (VCHECK=1, no PATH)
                m.read.return_value = REAL_BROTHER_RESPONSE_UP_TO_DATE
            elif call_count[0] == 2:
                # Second call: fallback with decremented version → PATH
                m.read.return_value = xml_update
            else:
                # Third call: firmware download — 200KB then done
                m.read.side_effect = [b"\x00" * 204800, b""]
                m.headers.get.return_value = None
            return m

        monkeypatch.setattr(oh.urllib.request, "urlopen", mock_urlopen)
        monkeypatch.setattr("builtins.input", lambda _=None: None)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware("MAIN", "1.24")
        assert result == oh.EXIT_OK  # --test mode: downloaded + verified
        assert call_count[0] >= 3  # original + fallback + download

    def test_vcheck1_fallback_fails(self, monkeypatch):
        """VCHECK=1 with --reflash, fallback also VCHECK=1 → EXIT_VENDOR."""
        from unittest.mock import MagicMock
        from types import SimpleNamespace

        oh.args = SimpleNamespace(
            beta=False, verbose=False, test=False, yes=True,
            ip="1.2.3.4", password=None, reflash=True,
        )
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        call_count = [0]

        def mock_urlopen(req, timeout=None):
            call_count[0] += 1
            m = MagicMock()
            m.read.return_value = REAL_BROTHER_RESPONSE_UP_TO_DATE
            return m

        monkeypatch.setattr(oh.urllib.request, "urlopen", mock_urlopen)
        monkeypatch.setattr("builtins.input", lambda _=None: None)

        result = oh.update_firmware("MAIN", "1.24")
        assert result == oh.EXIT_VENDOR
        assert call_count[0] == 2  # Original + fallback, both VCHECK=1


# ---------------------------------------------------------------------------
# _decrement_version
# ---------------------------------------------------------------------------

class TestDecrementVersion:
    def test_normal_version(self):
        assert oh._decrement_version("1.24") == "1.23"

    def test_zero_minor(self):
        """Minor version 0 — can't decrement further."""
        assert oh._decrement_version("1.00") is None

    def test_three_part_version(self):
        assert oh._decrement_version("2.10.5") == "2.9.5"

    def test_non_numeric(self):
        assert oh._decrement_version("abc") is None

    def test_empty(self):
        assert oh._decrement_version("") is None


# ---------------------------------------------------------------------------
# _validate_firmware_url
# ---------------------------------------------------------------------------

class TestValidateFirmwareUrl:
    def test_valid_brother_cdn_http(self):
        """Standard Brother CDN URL over HTTP — should pass."""
        valid, err = oh._validate_firmware_url(
            "http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf"
        )
        assert valid is True
        assert err is None

    def test_valid_brother_cdn_https(self):
        """HTTPS variant — should pass."""
        valid, err = oh._validate_firmware_url(
            "https://update-akamai.brother.co.jp/CS/D00XXX_A.djf"
        )
        assert valid is True
        assert err is None

    def test_valid_upd_extension(self):
        """.upd files are valid Brother firmware."""
        valid, err = oh._validate_firmware_url(
            "http://download.brother.com/pub/HL1110_SUB1.upd"
        )
        assert valid is True

    def test_wrong_domain(self):
        """Non-Brother domain — should fail."""
        valid, err = oh._validate_firmware_url(
            "http://evil.com/firmware.djf"
        )
        assert valid is False
        assert "domain" in err.lower()

    def test_file_scheme(self):
        """file:// URLs are not allowed."""
        valid, err = oh._validate_firmware_url(
            "file:///tmp/firmware.djf"
        )
        assert valid is False
        assert "scheme" in err.lower()

    def test_wrong_extension(self):
        """Non-firmware extensions — should fail."""
        valid, err = oh._validate_firmware_url(
            "http://update-akamai.brother.co.jp/CS/readme.txt"
        )
        assert valid is False
        assert "file type" in err.lower() or "extension" in err.lower()

    def test_empty_url(self):
        """Empty URL — should fail."""
        valid, err = oh._validate_firmware_url("")
        assert valid is False

    def test_url_with_query_params(self):
        """URL with query params — should still validate (extension before ?)."""
        valid, err = oh._validate_firmware_url(
            "http://update-akamai.brother.co.jp/CS/D00XXX_A.djf?token=abc"
        )
        assert valid is True


# ---------------------------------------------------------------------------
# TCP upload (sendfile retry)
# ---------------------------------------------------------------------------

class TestTcpUpload:
    """Tests for the TCP upload retry loop."""

    def test_sendfile_full_success(self, monkeypatch, tmp_path):
        """sendfile returns full file size in one call — success."""
        from unittest.mock import MagicMock

        fw_path = tmp_path / "test.djf"
        fw_path.write_bytes(b"\x00" * 4096)

        mock_sock = MagicMock()
        mock_sock.sendfile.return_value = 4096

        result = oh._tcp_upload(str(fw_path), "1.2.3.4", mock_sock)
        assert result is True
        assert mock_sock.sendfile.call_count == 1

    def test_sendfile_short_write_retry(self, monkeypatch, tmp_path):
        """sendfile returns partial bytes — retries until complete."""
        from unittest.mock import MagicMock

        fw_path = tmp_path / "test.djf"
        fw_path.write_bytes(b"\x00" * 8192)

        mock_sock = MagicMock()
        mock_sock.sendfile.side_effect = [4096, 4096, 0]

        result = oh._tcp_upload(str(fw_path), "1.2.3.4", mock_sock)
        assert result is True
        assert mock_sock.sendfile.call_count >= 2

    def test_sendfile_zero_return_fails(self, monkeypatch, tmp_path):
        """sendfile returns 0 — connection closed, should fail."""
        from unittest.mock import MagicMock

        fw_path = tmp_path / "test.djf"
        fw_path.write_bytes(b"\x00" * 4096)

        mock_sock = MagicMock()
        mock_sock.sendfile.return_value = 0

        result = oh._tcp_upload(str(fw_path), "1.2.3.4", mock_sock)
        assert result is False


# ---------------------------------------------------------------------------
# _verify_firmware_integrity
# ---------------------------------------------------------------------------

class TestVerifyFirmwareIntegrity:
    """Tests for firmware file integrity checks."""

    def test_content_length_match(self, tmp_path):
        """Content-Length matches file size — passes."""
        fw_path = tmp_path / "test.djf"
        fw_path.write_bytes(b"\x00" * 204800)  # 200KB

        valid, err = oh._verify_firmware_integrity(
            str(fw_path), content_length=204800
        )
        assert valid is True
        assert err is None

    def test_content_length_mismatch(self, tmp_path):
        """Content-Length doesn't match — fails."""
        fw_path = tmp_path / "test.djf"
        fw_path.write_bytes(b"\x00" * 200000)

        valid, err = oh._verify_firmware_integrity(
            str(fw_path), content_length=300000
        )
        assert valid is False
        assert "size mismatch" in err.lower()

    def test_file_too_small(self, tmp_path):
        """File smaller than 100KB minimum — fails."""
        fw_path = tmp_path / "test.djf"
        fw_path.write_bytes(b"\x00" * 50000)  # 50KB

        valid, err = oh._verify_firmware_integrity(str(fw_path))
        assert valid is False
        assert "too small" in err.lower()

    def test_minimum_size_pass(self, tmp_path):
        """File at exactly 100KB — passes with no Content-Length."""
        fw_path = tmp_path / "test.djf"
        fw_path.write_bytes(b"\x00" * 102400)  # 100KB

        valid, err = oh._verify_firmware_integrity(str(fw_path))
        assert valid is True

    def test_size_gate_only(self, tmp_path):
        """No Content-Length provided — uses size gate only."""
        fw_path = tmp_path / "test.djf"
        fw_path.write_bytes(b"\x00" * 500000)  # 500KB

        valid, err = oh._verify_firmware_integrity(str(fw_path))
        assert valid is True


# ---------------------------------------------------------------------------
# _http_post — HTTP error handling (P1: SSL #42, P2: HTTP errors #51)
# ---------------------------------------------------------------------------

class TestHttpPost:
    """Tests for _http_post error handling."""

    def test_success(self, monkeypatch):
        """Successful POST returns response bytes."""
        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<xml>ok</xml>"

        def fake_urlopen(req, timeout=None, context=None):
            return mock_resp

        monkeypatch.setattr(oh.urllib.request, "urlopen", fake_urlopen)

        data, err = oh._http_post("https://test.local", b"<req/>",
                                  {"Content-Type": "text/xml"})
        assert data == b"<xml>ok</xml>"
        assert err is None

    def test_http_503(self, monkeypatch):
        """HTTP 503 returns None + error message."""
        from unittest.mock import MagicMock
        import urllib.error

        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.HTTPError(
                "https://test.local", 503, "Service Unavailable", {}, None
            )

        monkeypatch.setattr(oh.urllib.request, "urlopen", fake_urlopen)

        data, err = oh._http_post("https://test.local", b"<req/>",
                                  {"Content-Type": "text/xml"})
        assert data is None
        assert "503" in err
        assert "brother server" in err.lower()

    def test_ssl_cert_error(self, monkeypatch):
        """SSL certificate error returns None + cert guidance."""
        from unittest.mock import MagicMock
        import urllib.error
        import ssl

        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.URLError(
                ssl.SSLCertVerificationError(
                    "certificate verify failed: unable to get local issuer certificate"
                )
            )

        monkeypatch.setattr(oh.urllib.request, "urlopen", fake_urlopen)

        data, err = oh._http_post("https://test.local", b"<req/>",
                                  {"Content-Type": "text/xml"})
        assert data is None
        assert "ssl" in err.lower() or "certificate" in err.lower()

    def test_timeout(self, monkeypatch):
        """Socket timeout returns None + network guidance."""
        from unittest.mock import MagicMock
        import urllib.error
        import socket

        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.URLError(socket.timeout("timed out"))

        monkeypatch.setattr(oh.urllib.request, "urlopen", fake_urlopen)

        data, err = oh._http_post("https://test.local", b"<req/>",
                                  {"Content-Type": "text/xml"})
        assert data is None
        assert "timeout" in err.lower() or "network" in err.lower()

    def test_dns_failure(self, monkeypatch):
        """DNS failure returns None + network guidance."""
        from unittest.mock import MagicMock
        import urllib.error
        import socket

        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.URLError(
                socket.gaierror("Name or service not known")
            )

        monkeypatch.setattr(oh.urllib.request, "urlopen", fake_urlopen)

        data, err = oh._http_post("https://test.local", b"<req/>",
                                  {"Content-Type": "text/xml"})
        assert data is None
        assert "network" in err.lower() or "dns" in err.lower() or "connect" in err.lower()


# ---------------------------------------------------------------------------
# Safety gates: exit codes, already-current gate, downgrade block, TTY gate
# ---------------------------------------------------------------------------

XML_UPDATE_124 = (
    b'<?xml version="1.0" encoding="UTF-8" ?>'
    b'<RESPONSEINFO>'
    b'<FIRMUPDATEINFO>'
    b'<VERSIONCHECK>0</VERSIONCHECK>'
    b'<PATH>http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf</PATH>'
    b'</FIRMUPDATEINFO>'
    b'</RESPONSEINFO>'
)

XML_UPDATE_120 = (
    b'<?xml version="1.0" encoding="UTF-8" ?>'
    b'<RESPONSEINFO>'
    b'<FIRMUPDATEINFO>'
    b'<VERSIONCHECK>0</VERSIONCHECK>'
    b'<PATH>http://update-akamai.brother.co.jp/CS/D02FZM_120Q_crypt.djf</PATH>'
    b'</FIRMUPDATEINFO>'
    b'</RESPONSEINFO>'
)


def _args(**overrides):
    from types import SimpleNamespace
    base = dict(beta=False, verbose=False, test=False, yes=True,
                ip="1.2.3.4", password=None, reflash=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def _download_response(size=204800):
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.read.side_effect = [b"\x00" * size, b""]
    resp.headers.get.return_value = None
    return resp


class TestSafetyGates:
    """End-to-end guards on the flash path."""

    def test_vcheck1_does_not_upload_without_reflash(self, monkeypatch, capsys):
        """VERSIONCHECK=1 without --reflash: no fallback, no download, no upload."""
        oh.args = _args(reflash=False)
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (REAL_BROTHER_RESPONSE_UP_TO_DATE, None))

        fallback_calls = []
        monkeypatch.setattr(oh, "_try_version_fallback",
                            lambda *a, **k: fallback_calls.append(1) or None)

        def no_download(*a, **k):
            raise AssertionError("firmware must not be downloaded")

        def no_socket(*a, **k):
            raise AssertionError("no socket may be opened")

        monkeypatch.setattr(oh.urllib.request, "urlopen", no_download)
        monkeypatch.setattr(oh.socket, "socket", no_socket)
        monkeypatch.setattr(oh.socket, "getaddrinfo", no_socket)

        assert oh.update_firmware("MAIN", "1.24") == oh.EXIT_CURRENT
        assert fallback_calls == []
        out = capsys.readouterr().out
        assert "already up to date" in out.lower()

    def test_reflash_flag_enables_current_version_upload(self, monkeypatch, tmp_path):
        """With --reflash the fallback path is allowed to run."""
        oh.args = _args(reflash=True, test=True)
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        api_calls = []

        def fake_post(url, data, hdrs, *a, **k):
            api_calls.append(1)
            if len(api_calls) == 1:
                return REAL_BROTHER_RESPONSE_UP_TO_DATE, None
            return XML_UPDATE_124, None

        monkeypatch.setattr(oh, "_http_post", fake_post)
        monkeypatch.setattr(oh.urllib.request, "urlopen",
                            lambda *a, **k: _download_response())
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware("MAIN", "1.24") == oh.EXIT_OK
        assert len(api_calls) == 2  # original + fallback, proving fallback ran

    def test_downgrade_blocked_when_artifact_older_than_installed(
            self, monkeypatch, tmp_path, capsys):
        """Artifact 1.20 vs installed 1.24 → refused, nothing downloaded."""
        oh.args = _args()
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_120, None))

        def no_download(*a, **k):
            raise AssertionError("downgrade artifact must not be downloaded")

        monkeypatch.setattr(oh.urllib.request, "urlopen", no_download)
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware("MAIN", "1.24") == oh.EXIT_REFUSED
        out = capsys.readouterr().out
        assert "1.20" in out and "1.24" in out

    def test_upload_failure_exits_nonzero(self, monkeypatch, tmp_path):
        """A failed upload yields EXIT_UPLOAD (non-zero)."""
        from unittest.mock import MagicMock

        oh.args = _args()
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, "urlopen",
                            lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, "_tcp_upload", lambda f, ip, sock: False)
        monkeypatch.setattr(oh.socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, '', ('1.2.3.4', 9100))])
        monkeypatch.setattr(oh.socket, "socket", lambda *a: MagicMock())
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware("MAIN", "1.24")
        assert result == oh.EXIT_UPLOAD
        assert result != oh.EXIT_OK

    def test_upload_failure_message_is_not_no_update_needed(self, monkeypatch, capsys):
        """A failed upload must never be reported as 'nothing needed'."""
        async def fake_walk(*a, **k):
            return [[(str(o), str(v)) for o, v in row]
                    for row in REAL_SNMP_TABLE]

        monkeypatch.setattr(oh, "_snmp_walk_table", fake_walk)
        monkeypatch.setattr(oh, "update_firmware", lambda c, v: oh.EXIT_UPLOAD)
        monkeypatch.setattr("builtins.input", lambda _=None: None)
        monkeypatch.setattr("sys.argv", ["oh-brother.py", "--yes", "1.2.3.4"])

        code = oh.main()
        out = capsys.readouterr().out
        assert code == oh.EXIT_UPLOAD
        assert "No firmware update was needed" not in out
        assert "FAILURE" in out

    def test_non_tty_without_yes_refuses_to_flash(self, monkeypatch, tmp_path):
        """stdin not a TTY and no --yes → no socket is ever created."""
        class FakeStdin:
            def isatty(self):
                return False

        oh.args = _args(yes=False)
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh.sys, "stdin", FakeStdin())
        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, "urlopen",
                            lambda *a, **k: _download_response())

        def no_socket(*a, **k):
            raise AssertionError("must refuse before opening a socket")

        monkeypatch.setattr(oh.socket, "socket", no_socket)
        monkeypatch.setattr(oh.socket, "getaddrinfo", no_socket)
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware("MAIN", "1.24") == oh.EXIT_REFUSED

    def test_validate_url_rejects_suffix_domain(self):
        """Look-alike domains must not pass the allow-list."""
        for url in ("http://evilbrother.com/CS/x.djf",
                    "http://notbrother.com/CS/x.djf"):
            valid, err = oh._validate_firmware_url(url)
            assert valid is False, url
            assert "domain" in err.lower()

    def test_missing_model_or_spec_aborts_before_api(self, monkeypatch, capsys):
        """model=None aborts before any vendor request is made."""
        oh.args = _args()
        oh.model = None
        oh.spec = "0906"

        def no_post(*a, **k):
            raise AssertionError("vendor API must not be called")

        monkeypatch.setattr(oh, "_http_post", no_post)

        assert oh.update_firmware("MAIN", "1.24") == oh.EXIT_REFUSED
        out = capsys.readouterr().out
        assert "REFUSING" in out

    def test_parse_artifact_version(self):
        """Version extraction from real Brother artifact names."""
        assert oh._parse_artifact_version("D02FZM_124Q_crypt.djf") == "1.24"
        assert oh._parse_artifact_version("D00KJY_F") is None
        assert oh._parse_artifact_version("LZ2751_L") is None


# ---------------------------------------------------------------------------
# Firmware write-path hardening: retention, incomplete transfer, verification
# ---------------------------------------------------------------------------

def _fake_tcp_socket(monkeypatch, sendfile_side_effect=None):
    """Install a fake raw-TCP socket and address lookup for update_firmware."""
    from unittest.mock import MagicMock

    sock = MagicMock()
    sock.__enter__.return_value = sock
    if sendfile_side_effect is not None:
        sock.sendfile.side_effect = sendfile_side_effect
    monkeypatch.setattr(
        oh.socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, '', ('1.2.3.4', 9100))])
    monkeypatch.setattr(oh.socket, "socket", lambda *a: sock)
    return sock


class TestFlashHardening:
    """Recovery-image retention, incomplete transfers, post-flash checks."""

    def test_image_retained_on_upload_failure(self, monkeypatch, tmp_path):
        """A failed upload keeps the image for a retry."""
        oh.args = _args()
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, "urlopen",
                            lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, "_tcp_upload", lambda f, ip, sock: False)
        _fake_tcp_socket(monkeypatch)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware("MAIN", "1.24")
        assert result == oh.EXIT_UPLOAD

        found = list(tmp_path.rglob("*.djf"))
        assert len(found) == 1
        assert found[0].read_bytes()

    def test_test_mode_retains_artifact(self, monkeypatch, tmp_path):
        """--test leaves the downloaded image on disk (it is the backup)."""
        oh.args = _args(test=True)
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, "urlopen",
                            lambda *a, **k: _download_response())
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware("MAIN", "1.24") == oh.EXIT_OK
        assert list(tmp_path.rglob("*.djf"))
        assert not list(tmp_path.rglob("*.part"))

    def test_no_partial_file_under_real_firmware_name(self, monkeypatch, tmp_path):
        """An interrupted download leaves no file at the real firmware name."""
        from unittest.mock import MagicMock

        oh.args = _args()
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_124, None))

        resp = MagicMock()
        resp.headers.get.return_value = None
        resp.read.side_effect = [b"\x00" * 102400, OSError("connection reset")]
        monkeypatch.setattr(oh.urllib.request, "urlopen", lambda *a, **k: resp)
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware("MAIN", "1.24") == oh.EXIT_DOWNLOAD
        assert not (tmp_path / "D02FZM_124Q_crypt.djf").exists()
        assert not list(tmp_path.rglob("*.part"))

    def test_sendfile_timeout_after_progress_marked_incomplete(
            self, monkeypatch, tmp_path, capsys):
        """A timeout after bytes were accepted is INCOMPLETE, not a failure."""
        oh.args = _args()
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, "urlopen",
                            lambda *a, **k: _download_response())
        _fake_tcp_socket(monkeypatch, sendfile_side_effect=[4096, TimeoutError("timed out")])
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware("MAIN", "1.24")
        out = capsys.readouterr().out

        assert result == oh.EXIT_UPLOAD
        assert result != oh.EXIT_OK
        assert "INCOMPLETE" in out
        assert "DO NOT POWER OFF" in out
        assert list(tmp_path.rglob("*.djf"))  # retained for reflash

    def test_post_upload_version_verified_via_snmp(
            self, monkeypatch, tmp_path, capsys):
        """A matching post-flash version is a verified success."""
        oh.args = _args(community="public")
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, "urlopen",
                            lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, "_tcp_upload", lambda f, ip, sock: True)
        monkeypatch.setattr(oh, "_query_printer_version",
                            lambda ip, community, cat: "1.24")
        _fake_tcp_socket(monkeypatch)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware("MAIN", "1.24")
        out = capsys.readouterr().out

        assert result == oh.EXIT_OK
        assert "expected=1.24 actual=1.24" in out
        assert not list(tmp_path.rglob("*.djf"))  # deleted after verified flash

    def test_post_upload_version_mismatch_fails(
            self, monkeypatch, tmp_path, capsys):
        """A completed read with the wrong version is a real failure."""
        oh.args = _args()
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, "urlopen",
                            lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, "_tcp_upload", lambda f, ip, sock: True)
        monkeypatch.setattr(oh, "_query_printer_version",
                            lambda ip, community, cat: "1.20")
        _fake_tcp_socket(monkeypatch)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware("MAIN", "1.24")
        out = capsys.readouterr().out

        assert result == oh.EXIT_UPLOAD
        assert "expected=1.24 actual=1.20" in out
        assert list(tmp_path.rglob("*.djf"))  # retained on real failure

    def test_post_upload_unverifiable_is_not_failure(
            self, monkeypatch, tmp_path, capsys):
        """Not coming back within the deadline is UNVERIFIED, not a failure."""
        oh.args = _args()
        oh.model = "HL-L2865DW"
        oh.spec = "0906"

        monkeypatch.setattr(oh, "FLASH_VERIFY_TIMEOUT", 0)
        monkeypatch.setattr(oh.time, "sleep", lambda s: None)
        monkeypatch.setattr(oh, "_http_post",
                            lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, "urlopen",
                            lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, "_tcp_upload", lambda f, ip, sock: True)
        monkeypatch.setattr(oh, "_query_printer_version",
                            lambda ip, community, cat: None)
        _fake_tcp_socket(monkeypatch)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware("MAIN", "1.24")
        out = capsys.readouterr().out

        assert result == oh.EXIT_UNVERIFIED
        assert result != oh.EXIT_UPLOAD
        assert "could not be verified" in out
        assert "FAILURE" not in out
        assert list(tmp_path.rglob("*.djf"))  # retained


# ---------------------------------------------------------------------------
# Interrupt handling (R19) and printer-unreachable classification (R15)
# ---------------------------------------------------------------------------

def _prepare_flash(monkeypatch, tmp_path):
    """Point update_firmware at a valid API response and a fake download."""
    oh.args = _args()
    oh.model = "HL-L2865DW"
    oh.spec = "0906"
    monkeypatch.setattr(oh, "_http_post",
                        lambda *a, **k: (XML_UPDATE_124, None))
    monkeypatch.setattr(oh.urllib.request, "urlopen",
                        lambda *a, **k: _download_response())
    monkeypatch.chdir(tmp_path)


class TestInterruptHandling:
    """A Ctrl-C must never brick a printer nor escape as a traceback."""

    def test_ctrl_c_during_upload_warns_and_retains_image(
            self, monkeypatch, tmp_path, capsys):
        """Interrupted mid-transfer: loud warning, image kept, exit 7."""
        _prepare_flash(monkeypatch, tmp_path)

        def interrupt(f, ip, sock):
            raise KeyboardInterrupt()

        monkeypatch.setattr(oh, "_tcp_upload", interrupt)
        _fake_tcp_socket(monkeypatch)

        result = oh.update_firmware("MAIN", "1.24")
        out = capsys.readouterr().out

        assert result == oh.EXIT_UPLOAD
        assert "INTERRUPTED DURING UPLOAD" in out
        assert "DO NOT TURN THE PRINTER OFF" in out
        assert "Traceback" not in out
        # The recovery image is the whole point of retaining it.
        assert len(list(tmp_path.rglob("*.djf"))) == 1

    def test_ctrl_c_during_verification_is_not_a_success(
            self, monkeypatch, tmp_path, capsys):
        """Interrupted during the confirm poll: UNVERIFIED, never exit 0."""
        _prepare_flash(monkeypatch, tmp_path)
        monkeypatch.setattr(oh, "_tcp_upload", lambda f, ip, sock: True)
        _fake_tcp_socket(monkeypatch)

        def interrupt(*a, **k):
            raise KeyboardInterrupt()

        monkeypatch.setattr(oh, "_verify_flash", interrupt)

        result = oh.update_firmware("MAIN", "1.24")
        out = capsys.readouterr().out

        assert result == oh.EXIT_UNVERIFIED
        assert result != oh.EXIT_OK
        assert "NOT confirmed" in out
        assert "Traceback" not in out
        assert list(tmp_path.rglob("*.djf"))  # retained, not deleted

    def test_ctrl_c_during_snmp_exits_cleanly(self, monkeypatch, capsys):
        """A Ctrl-C before the upload window is a clean 130."""
        async def interrupted_walk(*a, **k):
            raise KeyboardInterrupt()

        monkeypatch.setattr(oh, "_snmp_walk_table", interrupted_walk)
        monkeypatch.setattr("builtins.input", lambda _=None: None)
        monkeypatch.setattr("sys.argv", ["oh-brother.py", "--yes", "1.2.3.4"])

        code = oh.main()
        out = capsys.readouterr().out

        assert code == oh.EXIT_INTERRUPTED == 130
        assert "Interrupted." in out
        assert "Traceback" not in out


class TestPrinterUnreachable:
    """R15: an unresolvable or refusing printer is exit 4, not exit 1."""

    def test_unresolvable_host_returns_exit_printer(
            self, monkeypatch, tmp_path, capsys):
        _prepare_flash(monkeypatch, tmp_path)

        def no_resolve(*a, **k):
            raise oh.socket.gaierror("Name or service not known")

        monkeypatch.setattr(oh.socket, "getaddrinfo", no_resolve)

        result = oh.update_firmware("MAIN", "1.24")
        out = capsys.readouterr().out

        assert result == oh.EXIT_PRINTER == 4
        assert "Cannot reach the printer" in out
        # Nothing reached the printer, so the image must be kept.
        assert list(tmp_path.rglob("*.djf"))

    def test_refused_connection_returns_exit_printer(
            self, monkeypatch, tmp_path, capsys):
        from unittest.mock import MagicMock

        _prepare_flash(monkeypatch, tmp_path)

        sock = MagicMock()
        sock.connect.side_effect = ConnectionRefusedError("Connection refused")
        monkeypatch.setattr(
            oh.socket, "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, '', ('1.2.3.4', 9100))])
        monkeypatch.setattr(oh.socket, "socket", lambda *a: sock)

        result = oh.update_firmware("MAIN", "1.24")
        out = capsys.readouterr().out

        assert result == oh.EXIT_PRINTER
        assert "Cannot reach the printer" in out
        # A socket that never connected must not be leaked.
        assert sock.close.called

    def test_upload_failure_after_connect_is_still_exit_upload(
            self, monkeypatch, tmp_path, capsys):
        """Connectivity (4) and rejection (7) must stay distinguishable."""
        _prepare_flash(monkeypatch, tmp_path)
        monkeypatch.setattr(oh, "_tcp_upload", lambda f, ip, sock: False)
        _fake_tcp_socket(monkeypatch)

        result = oh.update_firmware("MAIN", "1.24")

        assert result == oh.EXIT_UPLOAD
        assert result != oh.EXIT_PRINTER


class TestSnmpFailureClassification:
    """An off or SNMP-disabled printer must report PRINTER (4), not ERROR (1).

    This was a sibling of the R15 flaw: _snmp_walk_table called sys.exit(1),
    so the most common real-world failure ("printer is off") reported an
    internal error and the documented exit code 4 was unreachable.
    """

    def test_walk_raises_snmp_error_on_no_response(self, monkeypatch):
        """The walk must raise, not kill the process with sys.exit()."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        async def fake_walk(*a, **k):
            yield ("RequestTimedOut", None, None, [])

        monkeypatch.setattr(oh, "walk_cmd", fake_walk)
        monkeypatch.setattr(
            oh, "UdpTransportTarget",
            SimpleNamespace(create=AsyncMock(return_value=None)))
        monkeypatch.setattr(oh, "SnmpDispatcher", lambda: None)
        monkeypatch.setattr(oh, "CommunityData", lambda *a, **k: None)
        monkeypatch.setattr(oh, "ObjectType", lambda *a: None)
        monkeypatch.setattr(oh, "ObjectIdentity", lambda *a: None)

        with pytest.raises(oh.SnmpError) as excinfo:
            asyncio.run(oh._snmp_walk_table("1.2.3.4", "public", "1.2.3.4"))

        assert excinfo.value.exit_code == oh.EXIT_PRINTER == 4
        assert "No SNMP response" in str(excinfo.value)

    def test_no_snmp_response_maps_to_exit_printer(self, monkeypatch, capsys):
        """main() surfaces the walk's code, so 'printer off' exits 4."""
        async def no_response(*a, **k):
            raise oh.SnmpError(
                "No SNMP response from 1.2.3.4 (RequestTimedOut).",
                oh.EXIT_PRINTER)

        monkeypatch.setattr(oh, "_snmp_walk_table", no_response)
        monkeypatch.setattr("sys.argv", ["oh-brother.py", "--yes", "1.2.3.4"])

        code = oh.main()
        err = capsys.readouterr().err

        assert code == oh.EXIT_PRINTER == 4
        assert "No SNMP response" in err

    def test_snmp_protocol_error_stays_exit_error(self, monkeypatch, capsys):
        """A printer that answers but errors is not 'unreachable'."""
        async def bad_response(*a, **k):
            raise oh.SnmpError(
                "SNMP error reading the printer: noSuchName at 1.2.3.4",
                oh.EXIT_ERROR)

        monkeypatch.setattr(oh, "_snmp_walk_table", bad_response)
        monkeypatch.setattr("sys.argv", ["oh-brother.py", "--yes", "1.2.3.4"])

        code = oh.main()
        err = capsys.readouterr().err

        assert code == oh.EXIT_ERROR == 1
        assert code != oh.EXIT_PRINTER

    def test_snmp_transport_uses_bounded_timeout_and_retries(self, monkeypatch):
        """The transport must state its budget, not inherit pysnmp's.

        pysnmp defaults to timeout=1, retries=5. The original code passed
        only timeout=30, so an unreachable printer cost 30*(5+1) = 180s.
        """
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        captured = {}

        async def fake_create(address, *a, **k):
            captured["address"] = address
            captured.update(k)
            return MagicMock()

        async def empty_walk(*a, **k):
            for row in ():
                yield row

        monkeypatch.setattr(oh, "UdpTransportTarget",
                            SimpleNamespace(create=fake_create))
        monkeypatch.setattr(oh, "walk_cmd", empty_walk)
        monkeypatch.setattr(oh, "SnmpDispatcher", lambda: None)
        monkeypatch.setattr(oh, "CommunityData", lambda *a, **k: None)
        monkeypatch.setattr(oh, "ObjectType", lambda *a: None)
        monkeypatch.setattr(oh, "ObjectIdentity", lambda *a: None)

        asyncio.run(oh._snmp_walk_table("1.2.3.4", "public", "1.2.3.4"))

        assert captured["address"] == ("1.2.3.4", 161)
        assert captured["timeout"] == oh.SNMP_TIMEOUT == 5
        assert captured["retries"] == oh.SNMP_RETRIES == 1
        # The regression was retries silently defaulting to 5.
        assert captured["retries"] < 5
        # Worst case per request must be well under the old three minutes.
        assert captured["timeout"] * (captured["retries"] + 1) <= 15

    def test_snmp_stage_is_bounded_by_a_deadline(self, monkeypatch, capsys):
        """A walk that never returns is cut off, reported as exit 4."""
        import asyncio
        import time

        monkeypatch.setattr(oh, "SNMP_DEADLINE", 0.05)

        async def hangs(*a, **k):
            await asyncio.sleep(30)

        monkeypatch.setattr(oh, "_snmp_walk_table", hangs)
        monkeypatch.setattr("sys.argv", ["oh-brother.py", "--yes", "1.2.3.4"])

        started = time.monotonic()
        code = oh.main()
        elapsed = time.monotonic() - started
        err = capsys.readouterr().err

        assert code == oh.EXIT_PRINTER
        assert "within" in err
        assert elapsed < 5, "SNMP_DEADLINE did not cut the walk off"

    def test_query_printer_version_tolerates_snmp_error(self, monkeypatch):
        """A rebooting printer must yield None, not raise.

        _verify_flash polls the printer while it is restarting, so SNMP
        silence during that window is expected. If changing the walk from
        sys.exit() to SnmpError broke this, a successful flash would be
        reported as a failure - the exact false negative the verification
        design exists to avoid.
        """
        async def silent(*a, **k):
            raise oh.SnmpError("No SNMP response", oh.EXIT_PRINTER)

        monkeypatch.setattr(oh, "_snmp_walk_table", silent)

        assert oh._query_printer_version("1.2.3.4", "public", "MAIN") is None

    def test_verify_flash_unverified_when_printer_stays_silent(
            self, monkeypatch):
        """Still-silent at the deadline is 'unverified', never a failure."""
        monkeypatch.setattr(oh, "FLASH_VERIFY_TIMEOUT", 0)
        monkeypatch.setattr(oh, "FLASH_VERIFY_POLL", 0)
        monkeypatch.setattr(oh, "_query_printer_version",
                            lambda ip, community, cat: None)

        status, actual = oh._verify_flash("1.2.3.4", "public", "MAIN", "1.24")

        assert status == "unverified"
        assert actual is None
