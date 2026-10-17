import unittest
from unittest.mock import MagicMock, patch

from capture_oshpcf import StartReads, request_semp_read


class StartReadsTest(unittest.TestCase):
    def test_ten_reads_sixty_seconds_apart(self):
        state = StartReads()
        state.sample(22, 0)
        self.assertTrue(state.sample(51, 1))
        reads = []
        for second in range(1, 660):
            state.sample(70, second)
            index = state.due(second)
            if index is not None:
                reads.append((second, index))
        self.assertEqual(reads, [(1 + 60 * index, index + 1) for index in range(10)])

    def test_no_start_without_valid_low_baseline(self):
        state = StartReads()
        self.assertFalse(state.sample(500, 0))
        self.assertIsNone(state.due(0))
        state.sample(50, 1)
        self.assertTrue(state.sample(50.1, 2))

    def test_stale_and_disconnect_cancel_series(self):
        state = StartReads()
        state.sample(20, 0)
        state.sample(60, 1)
        self.assertIsNone(state.due(17))
        self.assertFalse(state.sample(60, 18))
        state.sample(20, 19)
        state.sample(60, 20)
        state.reset()
        self.assertIsNone(state.due(21))

    def test_short_dips_do_not_rearm(self):
        state = StartReads()
        state.sample(20, 0)
        state.sample(60, 1)
        for second in range(1, 602):
            state.sample(60, second)
            state.due(second)
        state.sample(49, 602)
        self.assertFalse(state.sample(51, 603))
        for second in range(604, 665):
            state.sample(49, second)
        self.assertTrue(state.sample(51, 665))

    def test_no_catchup_burst(self):
        state = StartReads()
        state.sample(20, 0)
        state.sample(60, 1)
        self.assertEqual(state.due(1), 1)
        for second in range(2, 90):
            state.sample(60, second)
        self.assertEqual(state.due(89), 2)
        self.assertIsNone(state.due(89))

    @patch("capture_oshpcf.urllib.request.urlopen")
    def test_pending_read_never_presses_button(self, urlopen):
        response = MagicMock()
        response.read.return_value = b'{"value":"Warte auf Antwort"}'
        urlopen.return_value.__enter__.return_value = response
        self.assertEqual(request_semp_read("device.example.invalid"), "skipped: Warte auf Antwort")
        self.assertEqual(urlopen.call_count, 1)

    @patch("capture_oshpcf.urllib.request.urlopen")
    def test_only_read_button_can_be_pressed(self, urlopen):
        response = MagicMock()
        response.read.return_value = b'{"value":"Antwort ohne Alternativen"}'
        urlopen.return_value.__enter__.return_value = response
        self.assertIn("requested", request_semp_read("device.example.invalid"))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "http://device.example.invalid/button/esphome_hems_eg1_osshpcf_daten_lesen/press")


if __name__ == "__main__":
    unittest.main()