"""Synthetic SFTP objects only; no credentials, robot or ROS are used."""
import ast
import io
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parents[1]))
import read_robot_calibration as reader


class FactoryReader(unittest.TestCase):
    def sftp(self, values=(b"synthetic", b"synthetic"), *, mode=stat.S_IFREG, size=9, mutate=False):
        owner = self
        calls, opened = [], []
        class Sftp:
            def lstat(self, path):
                calls.append(path)
                return NS(st_mode=mode, st_size=size, st_mtime=1 if not mutate or len(calls) == 1 else 2)
            def open(self, path, mode):
                owner.assertEqual(reader.FACTORY_PATH, path)
                owner.assertEqual("rb", mode)
                stream = io.BytesIO(values[len(opened)])
                opened.append(stream)
                return stream
        return Sftp(), opened

    def test_only_documented_file_is_read_twice_and_handles_close(self):
        sftp, streams = self.sftp()
        self.assertEqual(b"synthetic", reader.read_snapshot(sftp))
        self.assertEqual(2, len(streams))
        self.assertTrue(all(stream.closed for stream in streams))

    def test_symlink_empty_oversized_and_directory_inputs_rejected_before_open(self):
        for options in ({"mode": stat.S_IFLNK}, {"mode": stat.S_IFDIR},
                        {"size": 0}, {"size": reader.MAXIMUM_BYTES + 1}):
            sftp, streams = self.sftp(**options)
            with self.assertRaises(ValueError):
                reader.read_snapshot(sftp)
            self.assertEqual([], streams)

    def test_content_change_short_read_and_metadata_change_fail_closed(self):
        for options in ({"values": (b"synthetic", b"SYnthetic")},
                        {"values": (b"short", b"short")}, {"mutate": True}):
            sftp, streams = self.sftp(**options)
            with self.assertRaises(ValueError):
                reader.read_snapshot(sftp)
            self.assertTrue(all(stream.closed for stream in streams))

    def test_existing_output_preserved_before_connection(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(reader, "connect") as connect:
            with self.assertRaises(ValueError):
                reader.run(NS(output=Path(directory), user="synthetic"))
            connect.assert_not_called()

    def test_reader_has_no_remote_command_or_write_methods(self):
        tree = ast.parse(Path(reader.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ("exec_command", "put", "remove", "rmdir", "chown"))
                if node.func.attr == "mkdir":
                    self.assertIsInstance(node.func.value, ast.Name)
                    self.assertEqual("output", node.func.value.id)
                if node.func.attr == "chmod":
                    self.assertIsInstance(node.func.value, ast.Name)
                    self.assertEqual("destination", node.func.value.id)


if __name__ == "__main__":
    unittest.main()
