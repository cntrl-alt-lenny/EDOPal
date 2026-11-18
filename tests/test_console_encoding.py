import io
import unittest
from unittest import mock

import main


class StdoutStub:
    def __init__(self, encoding: str | None):
        self.encoding = encoding


class ConsoleEncodingTests(unittest.TestCase):
    def test_windowed_output_survives_a_detached_parent_console(self):
        stream = io.StringIO()
        output = main._WindowedOutput(stream)
        self.assertEqual(output.write("working"), 7)
        self.assertEqual(stream.getvalue(), "working")
        stream.close()
        self.assertEqual(output.write("complete"), 8)
        output.flush()

    def test_unicode_output_supported_for_utf8(self):
        with mock.patch.object(main.sys, "stdout", StdoutStub("utf-8")):
            self.assertTrue(main._stdout_supports_unicode())

    def test_unicode_output_disabled_for_cp1252(self):
        with mock.patch.object(main.sys, "stdout", StdoutStub("cp1252")):
            self.assertFalse(main._stdout_supports_unicode())

    def test_unicode_output_disabled_when_windowed_build_has_no_output_stream(self):
        with (
            mock.patch.object(main.sys, "stdout", None),
            mock.patch.object(main.sys, "__stdout__", None),
        ):
            self.assertFalse(main._stdout_supports_unicode())


if __name__ == "__main__":
    unittest.main()
