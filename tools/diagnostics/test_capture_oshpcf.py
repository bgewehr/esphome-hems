import unittest
from unittest.mock import MagicMock, patch

from capture_oshpcf import RELEVANT_LINE, CaptureStats, StartReads, request_semp_read


class CaptureStatsTest(unittest.TestCase):
    def test_registration_transport_logs_are_retained_without_false_confirmations(self):
        stats = CaptureStats()
        for line in (
            "[W][eebus-registration] SPINE registration tx: kind=binding msg_cnt=42 send_err=0",
            "[W][eebus-registration] SPINE registration result: msg_ref_present=true msg_ref=42 error_present=true error=0",
            "EG1 OSSHPCF binding table: msg_ref=43 entries=0 array=null",
            "EG1 OSSHPCF subscription entry[0] server: device=? entity=[2] feature=7 comparison=missing-device",
            "[E][fronius] Storage writer latched off: transaction expired: stage=read-timeout token=42 age_ms=10001",
            "'ESPHome HEMS Storage Writer Status' >> 'Schutzpause: timeout not confirmed'",
        ):
            with self.subTest(line=line):
                self.assertIsNotNone(RELEVANT_LINE.search(line))
                stats.observe(line)
        self.assertEqual((stats.subscription_confirmations, stats.binding_confirmations, stats.semp_messages), (0, 0, 0))

    def test_classifies_messages_without_counting_decoded_lines(self):
        stats = CaptureStats()
        stats.observe("EG1 OSSHPCF msg from peer: SempData (fn=104) cmd=notify")
        stats.observe("EG1 OSSHPCF msg from peer: SempData (fn=104) cmd=reply")
        stats.observe("EG1 OSSHPCF data: summary: alternatives=0")
        stats.observe("EG1 OSSHPCF read reply: msg_ref=1 fn=104 alternatives=0")
        self.assertEqual((stats.semp_messages, stats.notifications, stats.replies, stats.read_replies), (2, 1, 1, 1))

    def test_legacy_logs_do_not_prove_subscription_or_notification(self):
        stats = CaptureStats()
        stats.observe("EG1 OSSHPCF msg from peer: SempData (fn=104) data=present")
        stats.observe("EG1 OSSHPCF: remote SEMP entity 2 subscribe_err=0 read_err=0")
        self.assertEqual(stats.unclassified, 1)
        self.assertEqual(stats.legacy_subscription_checks, 1)
        self.assertEqual(stats.subscription_confirmations, 0)
        self.assertEqual(stats.notifications, 0)

    def test_only_remote_table_matches_count_as_confirmation(self):
        stats = CaptureStats()
        for kind in ("subscription", "binding"):
            stats.observe(f"EG1 OSSHPCF {kind} request: result=sent")
            stats.observe(f"EG1 OSSHPCF {kind} verified: present=false")
            stats.observe(f"EG1 OSSHPCF {kind} verification failed: err=1")
        self.assertEqual((stats.subscription_requests, stats.binding_requests), (1, 1))
        self.assertEqual((stats.subscription_confirmations, stats.binding_confirmations), (0, 0))
        stats.observe("EG1 OSSHPCF subscription verified: present=true")
        stats.observe("EG1 OSSHPCF binding verified: present=true")
        self.assertEqual((stats.subscription_confirmations, stats.binding_confirmations), (1, 1))


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