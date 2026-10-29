"""Synthetic coordinator checks; no network, robot, native engine or real CDR."""
import argparse
import json
from pathlib import Path, PurePosixPath
import shlex
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).parents[1]))
import run_glim_supervised_target as coordinator


def args(**changes):
    return argparse.Namespace(**{
        "operator_confirmed": True, "host": "synthetic.invalid", "user": "synthetic",
        "known_identity": "synthetic", "directory": "lingxi-next-validation-arm64-supervised-synthetic",
        "recorded_directory": "lingxi-next-validation-arm64-recorded-synthetic",
        "core_directory": "lingxi-next-validation-arm64-core-synthetic",
        "run_name": "glim-supervised-replay-synthetic", "hypothesis": "header_is_scan_start",
        "rate": 1., "output": Path("unused"), **changes,
    })


class CoordinatorContracts(unittest.TestCase):
    def test_confirmation_is_required_before_files_paths_or_network(self):
        for value in (None, False, 1, "true"):
            with self.subTest(value=value), patch.object(coordinator, "source_files") as files, \
                    patch.object(coordinator, "connect") as connect:
                with self.assertRaisesRegex(ValueError, "confirmation"):
                    coordinator.run(args(operator_confirmed=value))
                files.assert_not_called()
                connect.assert_not_called()

    def test_command_has_explicit_environment_recorded_only_input_and_bounded_parent(self):
        home = PurePosixPath("/home/synthetic")
        source, output, command = coordinator.paths(home, args())
        values = shlex.split(command)
        self.assertEqual(["timeout", "--signal=INT", "--kill-after=10s", "180s", "env", "-i"], values[:6])
        self.assertEqual(home / args().directory, source)
        self.assertEqual(home / ".cache/lingxi-next/glim-supervised-replay-synthetic", output)
        self.assertEqual(str(home / args().recorded_directory / "result-live"),
                         values[values.index("--reference") + 1])
        self.assertIn("--operator-confirmed", values)
        self.assertIn("OPENBLAS_NUM_THREADS=2", values)
        for prefix in ("ROS_DOMAIN_ID=", "ROS_DISTRO=", "RMW_IMPLEMENTATION=", "LD_PRELOAD="):
            self.assertFalse(any(value.startswith(prefix) for value in values))
        self.assertNotIn("--seconds", values)
        self.assertNotIn("glim_live_session.py", command)

    def test_bad_names_rates_and_path_aliases_are_rejected_without_connecting(self):
        changes = (
            {"directory": "../outside"}, {"run_name": "../outside"}, {"run_name": "result; command"},
            {"core_directory": args().recorded_directory}, {"directory": args().recorded_directory},
            {"rate": True}, {"rate": 2.}, {"hypothesis": "automatic"},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                coordinator.paths(PurePosixPath("/home/synthetic"), args(**change))

    def test_local_output_must_be_new_and_private_and_user_non_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in (args(user="root", output=root / ".local/new"),
                          args(output=root / "outside"), args(output=root / ".local")):
                (root / ".local").mkdir(exist_ok=True)
                with patch.object(coordinator, "ROOT", root), patch.object(coordinator, "connect") as connect:
                    with self.assertRaises(ValueError):
                        coordinator.run(value)
                    connect.assert_not_called()

    def test_staged_source_inventory_contains_unchanged_worker_and_current_entry(self):
        sources = coordinator.source_files()
        for name in coordinator.target.SOURCES:
            self.assertIn(name, sources)
        self.assertIn("tools/run_glim_supervised_target.py", sources)
        self.assertTrue(all(entry["bytes"] > 0 and len(entry["sha256"]) == 64 for entry in sources.values()))

    def test_failed_remote_command_is_preserved_without_retry_and_connection_is_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".local/synthetic-failure"
            client, sftp = MagicMock(), MagicMock()
            client.open_sftp.return_value.__enter__.return_value = sftp
            sftp.normalize.return_value = "/home/synthetic"
            sftp.lstat.side_effect = FileNotFoundError("synthetic_missing_report")
            with patch.object(coordinator, "ROOT", root), \
                    patch.object(coordinator, "source_files", return_value={}), \
                    patch.object(coordinator, "connect", return_value=client), \
                    patch.object(coordinator, "execute", return_value=(1, b"synthetic failure")) as execute:
                with self.assertRaisesRegex(RuntimeError, "replay_failed"):
                    coordinator.run(args(output=output))
            execute.assert_called_once()
            client.close.assert_called_once()
            report = json.loads((output / "collection-report.json").read_text())
            self.assertEqual("failed_or_interrupted", report["result"])
            self.assertEqual(1, report["exitCode"])
            self.assertEqual(b"synthetic failure", (output / "private-command-output.log").read_bytes())

    def test_changed_source_stops_before_remote_execution_and_closes_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tools/synthetic.py"
            source.parent.mkdir()
            source.write_bytes(b"# explicitly synthetic changed source\n")
            client, sftp = MagicMock(), MagicMock()
            client.open_sftp.return_value.__enter__.return_value = sftp
            sftp.normalize.return_value = "/home/synthetic"
            with patch.object(coordinator, "ROOT", root), \
                    patch.object(coordinator, "source_files",
                                 return_value={"tools/synthetic.py": {"sha256": "0" * 64}}), \
                    patch.object(coordinator, "connect", return_value=client), \
                    patch.object(coordinator, "execute") as execute:
                with self.assertRaisesRegex(ValueError, "source_changed"):
                    coordinator.run(args(output=root / ".local/synthetic-change"))
            execute.assert_not_called()
            client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
