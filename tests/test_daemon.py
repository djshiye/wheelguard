import errno
import unittest

import evdev

from wheelguard.daemon import describe


class Describe(unittest.TestCase):
    def test_uinput_not_writable(self):
        ex = evdev.UInputError('"/dev/uinput" cannot be opened for writing')
        self.assertEqual(describe(ex), "permission denied: /dev/uinput")

    def test_other_uinput_error(self):
        self.assertIn("not a character", describe(evdev.UInputError("not a character device")))

    def test_permission_denied_with_filename(self):
        ex = PermissionError(errno.EACCES, "Permission denied", "/dev/input/event2")
        self.assertEqual(describe(ex), "permission denied: /dev/input/event2")

    def test_device_gone(self):
        self.assertEqual(describe(OSError(errno.ENODEV, "No such device")), "mouse disconnected")


if __name__ == "__main__":
    unittest.main()
