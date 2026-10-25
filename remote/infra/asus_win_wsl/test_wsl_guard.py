from datetime import datetime
import unittest
import xml.etree.ElementTree as ET

from render_wsl_guard import NS, render


class WslGuardTests(unittest.TestCase):
    SID = "S-1-5-21-111-222-333-1001"

    def setUp(self):
        self.root = ET.fromstring(render(self.SID, "Ubuntu-26.04", "2026-10-17T00:00:00", "mingliang"))

    def value(self, path):
        return self.root.findtext(path, namespaces={"t": NS})

    def test_original_owner_without_password_or_elevation(self):
        self.assertEqual(self.value("t:Principals/t:Principal/t:UserId"), self.SID)
        self.assertEqual(self.value("t:Triggers/t:LogonTrigger/t:UserId"), self.SID)
        self.assertEqual(self.value("t:Principals/t:Principal/t:LogonType"), "S4U")
        self.assertEqual(self.value("t:Principals/t:Principal/t:RunLevel"), "LeastPrivilege")

    def test_one_unprivileged_caller_and_no_service_commands(self):
        actions = self.root.findall("t:Actions/t:Exec", {"t": NS})
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.value("t:Actions/t:Exec/t:Command"), r"C:\Windows\System32\wsl.exe")
        self.assertEqual(self.value("t:Actions/t:Exec/t:Arguments"),
                         "--distribution Ubuntu-26.04 --user mingliang --cd / --exec /usr/bin/sleep infinity")

    def test_boot_logon_and_bounded_periodic_recovery(self):
        self.assertEqual(self.value("t:Triggers/t:BootTrigger/t:Delay"), "PT30S")
        self.assertEqual(self.value("t:Triggers/t:CalendarTrigger/t:Repetition/t:Interval"), "PT5M")
        self.assertEqual(self.value("t:Triggers/t:CalendarTrigger/t:Repetition/t:Duration"), "P1D")
        self.assertEqual(self.value("t:Triggers/t:CalendarTrigger/t:Repetition/t:StopAtDurationEnd"), "false")
        datetime.fromisoformat(self.value("t:Triggers/t:CalendarTrigger/t:StartBoundary"))

    def test_no_three_day_timeout_duplicates_or_host_wake(self):
        self.assertEqual(self.value("t:Settings/t:ExecutionTimeLimit"), "PT0S")
        self.assertEqual(self.value("t:Settings/t:MultipleInstancesPolicy"), "IgnoreNew")
        self.assertEqual(self.value("t:Settings/t:WakeToRun"), "false")
        self.assertEqual(self.value("t:Settings/t:RunOnlyIfNetworkAvailable"), "false")
        self.assertEqual(self.value("t:Settings/t:StartWhenAvailable"), "true")

    def test_rejects_system_and_action_injection(self):
        for sid in ("SYSTEM", "S-1-5-18", "S-1-5-21-111-222-333-1001 & cmd"):
            with self.assertRaises(ValueError):
                render(sid, "Ubuntu-26.04", "2026-10-17T00:00:00", "mingliang")
        for distro in ("", "Ubuntu --shutdown", 'Ubuntu" & cmd', "../other"):
            with self.assertRaises(ValueError):
                render(self.SID, distro, "2026-10-17T00:00:00", "mingliang")
        for user in ("root", "nobody", "--shutdown", 'mingliang" --user root'):
            with self.assertRaises(ValueError):
                render(self.SID, "Ubuntu-26.04", "2026-10-17T00:00:00", user)

    def test_rejects_ambiguous_boundary_and_unresolved_placeholders(self):
        for boundary in ("invalid", "2026-10-17T00:00:00+08:00"):
            with self.assertRaises(ValueError):
                render(self.SID, "Ubuntu-26.04", boundary, "mingliang")
        self.assertNotIn(b"__", ET.tostring(self.root))

    def test_windows_unicode_file_and_bstr_agree(self):
        payload = render(self.SID, "Ubuntu-26.04", "2026-10-17T00:00:00", "mingliang")
        self.assertTrue(payload.startswith((b"\xff\xfe", b"\xfe\xff")))
        self.assertIn("encoding='utf-16'", payload.decode("utf-16"))
        self.assertEqual(ET.fromstring(payload).tag, f"{{{NS}}}Task")


if __name__ == "__main__":
    unittest.main()
