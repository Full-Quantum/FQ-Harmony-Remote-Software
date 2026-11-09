import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harmony_one.app import find_failed_harmony_usb_device, restart_usb_device_elevated

INSTANCE = "USB\\VID_046D&PID_C121\\5&3B5FD5D2&0&10"


def _completed(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


class FindFailedDeviceTests(unittest.TestCase):
    def test_returns_failed_device(self):
        payload = json.dumps({"InstanceId": INSTANCE, "Status": "Error"})
        with patch("harmony_one.app.subprocess.run", return_value=_completed(stdout=payload)):
            self.assertEqual(find_failed_harmony_usb_device(), (INSTANCE, "Error"))

    def test_returns_none_when_all_ok(self):
        with patch("harmony_one.app.subprocess.run", return_value=_completed(stdout="")):
            self.assertIsNone(find_failed_harmony_usb_device())

    def test_returns_none_on_query_failure(self):
        with patch(
            "harmony_one.app.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="powershell", timeout=10),
        ):
            self.assertIsNone(find_failed_harmony_usb_device())

    def test_returns_none_on_bad_json(self):
        with patch("harmony_one.app.subprocess.run", return_value=_completed(stdout="not json")):
            self.assertIsNone(find_failed_harmony_usb_device())


class RestartDeviceTests(unittest.TestCase):
    def test_success_and_failure_exit_codes(self):
        with patch("harmony_one.app.subprocess.run", return_value=_completed(0)) as run:
            self.assertTrue(restart_usb_device_elevated(INSTANCE))
            command = run.call_args.args[0][-1]
            self.assertIn(INSTANCE, command)
            self.assertIn("RunAs", command)
        with patch("harmony_one.app.subprocess.run", return_value=_completed(1)):
            self.assertFalse(restart_usb_device_elevated(INSTANCE))

    def test_rejects_suspicious_instance_ids(self):
        with patch("harmony_one.app.subprocess.run") as run:
            self.assertFalse(restart_usb_device_elevated("USB\\X'; Remove-Item foo"))
            run.assert_not_called()

    def test_declined_uac_or_timeout_returns_false(self):
        with patch(
            "harmony_one.app.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="powershell", timeout=120),
        ):
            self.assertFalse(restart_usb_device_elevated(INSTANCE))


if __name__ == "__main__":
    unittest.main()
