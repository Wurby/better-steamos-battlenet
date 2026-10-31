import unittest

from bnet_steam import vdf

from .helpers import CONFIG_VDF


class BinaryVdf(unittest.TestCase):
    def test_roundtrip_preserves_bytes_types_and_order(self):
        data = {"shortcuts": {"0": {"appid": 3617010538, "appname": "Ünïcode ✓", "exe": '"/x"',
                                    "IsHidden": 0, "tags": {"0": "a", "1": "b"}},
                              "1": {"appid": 5, "appname": "B"}}}
        raw = vdf.dumps(data)
        self.assertEqual(vdf.loads(raw), data)
        self.assertEqual(vdf.dumps(vdf.loads(raw)), raw)
        self.assertEqual(list(vdf.loads(raw)["shortcuts"]), ["0", "1"])
        self.assertTrue(vdf.roundtrips(raw))

    def test_garbage_raises_vdf_error(self):
        for bad in (b"", b"\x00abc", b"\x01key\x00no-terminator", b"\x09weird\x00"):
            with self.assertRaises(vdf.VdfError):
                vdf.loads(bad)

    def test_roundtrips_false_for_trailing_bytes_or_garbage(self):
        raw = vdf.dumps({"shortcuts": {}})
        self.assertFalse(vdf.roundtrips(raw + b"\x00"))
        self.assertFalse(vdf.roundtrips(b"nonsense"))


class SetCompatTool(unittest.TestCase):
    def test_adds_entry_with_matching_indent(self):
        out = vdf.set_compat_tool(CONFIG_VDF, 12345, "proton_11")
        self.assertIn('\t\t\t\t\t"12345"\n\t\t\t\t\t{\n\t\t\t\t\t\t"name"\t\t"proton_11"', out)
        self.assertTrue(vdf.braces_balanced(out))
        self.assertEqual(out.count("{"), CONFIG_VDF.count("{") + 1)

    def test_idempotent_and_keeps_existing_entries(self):
        once = vdf.set_compat_tool(CONFIG_VDF, 12345, "proton_11")
        self.assertEqual(vdf.set_compat_tool(once, 12345, "proton_11"), once)
        self.assertIn('"280220"', once)

    def test_value_equal_to_appid_is_not_mistaken_for_entry(self):
        text = CONFIG_VDF.replace('"proton_experimental"', '"777"')
        self.assertIn('"777"\n\t\t\t\t\t{', vdf.set_compat_tool(text, 777, "proton_11"))

    def test_missing_or_unclosed_section_raises(self):
        with self.assertRaises(vdf.VdfError):
            vdf.set_compat_tool('"Steam"\n{\n}\n', 1, "x")
        with self.assertRaises(vdf.VdfError):
            vdf.set_compat_tool('"CompatToolMapping"\n{\n', 1, "x")


if __name__ == "__main__":
    unittest.main()
