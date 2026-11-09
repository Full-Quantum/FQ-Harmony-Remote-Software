import unittest

from harmony_one.import_ir import IRImportError, parse_ir


class ParseIRTests(unittest.TestCase):
    def test_parses_flipper_parsed_signals(self):
        signals = parse_ir(
            "Filetype: IR signals file\n"
            "Version: 1\n"
            "# ignored comment\n"
            "name: Power\n"
            "type: parsed\n"
            "protocol: Samsung32\n"
            "address: 07 00 00 00\n"
            "command: 02 00 00 00\n"
        )

        self.assertEqual(
            signals,
            [
                {
                    "name": "Power",
                    "type": "parsed",
                    "protocol": "Samsung32",
                    "address": "07 00 00 00",
                    "command": "02 00 00 00",
                }
            ],
        )

    def test_rejects_raw_signal_without_dropping_data(self):
        with self.assertRaisesRegex(IRImportError, "only parsed signals"):
            parse_ir(
                "Filetype: IR signals file\n"
                "Version: 1\n"
                "name: Power\n"
                "type: raw\n"
                "frequency: 38000\n"
            )

    def test_rejects_missing_fields(self):
        with self.assertRaisesRegex(IRImportError, "missing: command"):
            parse_ir(
                "Filetype: IR signals file\n"
                "Version: 1\n"
                "name: Power\n"
                "type: parsed\n"
                "protocol: Samsung32\n"
                "address: 07 00 00 00\n"
            )

    def test_rejects_unsupported_header_version(self):
        with self.assertRaisesRegex(IRImportError, "Version: 1"):
            parse_ir(
                "Filetype: IR signals file\n"
                "Version: 2\n"
                "name: Power\n"
                "type: parsed\n"
                "protocol: Samsung32\n"
                "address: 07 00 00 00\n"
                "command: 02 00 00 00\n"
            )


if __name__ == "__main__":
    unittest.main()
