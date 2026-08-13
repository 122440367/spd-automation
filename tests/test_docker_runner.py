import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import docker_runner


class DockerRunnerTests(unittest.TestCase):
    def test_parse_daily_time(self):
        parsed = docker_runner.parse_daily_time("08:30")
        self.assertEqual((parsed.hour, parsed.minute), (8, 30))

    def test_parse_daily_time_rejects_invalid_value(self):
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            docker_runner.parse_daily_time("8am")

    def test_scheduled_for_keeps_timezone(self):
        now = datetime.now().astimezone()
        target = docker_runner.scheduled_for(
            now,
            docker_runner.parse_daily_time("08:00"),
        )
        self.assertEqual((target.hour, target.minute, target.second), (8, 0, 0))
        self.assertEqual(target.tzinfo, now.tzinfo)

    def test_run_date_is_persisted(self):
        state_file = MagicMock()
        temp_file = state_file.with_suffix.return_value
        state_file.read_text.return_value = "2026-08-13\n"
        with patch.object(docker_runner, "STATE_FILE", state_file):
            self.assertEqual(docker_runner.read_last_run_date(), "2026-08-13")
            docker_runner.record_run_date("2026-08-13")
        state_file.parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        temp_file.write_text.assert_called_once_with("2026-08-13", encoding="utf-8")
        temp_file.replace.assert_called_once_with(state_file)


if __name__ == "__main__":
    unittest.main()
