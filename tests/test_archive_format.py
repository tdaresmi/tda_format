from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from archive_format import (
    ArchiveError,
    ENCRYPTED_MAGIC,
    FORMAT_NAME,
    FORMAT_VERSION,
    PasswordError,
    TDAArchive,
)


class ArchiveFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_plain_zip_create_add_rename_delete_verify_extract(self) -> None:
        source = self.root / "input"
        (source / "notes" / "empty").mkdir(parents=True)
        (source / "notes" / "readme.txt").write_text("TDA Archive deneme\n", encoding="utf-8")
        (source / "notes" / "paper.pdf").write_bytes(b"%PDF-test-placeholder")
        loose = self.root / "loose.csv"
        loose.write_text("a,b\n1,2\n", encoding="utf-8")
        archive_path = self.root / "sample.tda"

        archive = TDAArchive.create(archive_path)
        self.assertFalse(archive.encrypted)
        archive.add_paths([source, loose])
        self.assertEqual(archive.file_count, 3)
        self.assertEqual(archive.folder_count, 3)
        self.assertEqual(archive.verify()[0], 3)

        # An unencrypted .tda is an ordinary, interoperable ZIP file.
        with zipfile.ZipFile(archive_path) as standard_zip:
            self.assertEqual(standard_zip.testzip(), None)
            manifest = json.loads(standard_zip.read("manifest.json"))
            self.assertEqual(manifest["format"], FORMAT_NAME)
            self.assertEqual(manifest["version"], FORMAT_VERSION)

        archive.rename("input/notes/readme.txt", "input/notes/guide.txt")
        self.assertIn("input/notes/guide.txt", {e["path"] for e in archive.entries})
        archive.rename("input/notes", "input/docs")
        self.assertIn("input/docs/paper.pdf", {e["path"] for e in archive.entries})
        archive.delete("loose.csv")
        self.assertNotIn("loose.csv", {e["path"] for e in archive.entries})
        archive.verify()

        destination = self.root / "out"
        extracted = archive.extract(destination, ["input/docs"])
        self.assertEqual((destination / "input/docs/guide.txt").read_text(encoding="utf-8"), "TDA Archive deneme\n")
        self.assertTrue((destination / "input/docs/empty").is_dir())
        self.assertTrue(extracted)
        archive.close()

    def test_conflicts_do_not_change_archive(self) -> None:
        one = self.root / "same.txt"
        two_dir = self.root / "other"
        two_dir.mkdir()
        two = two_dir / "same.txt"
        one.write_text("one", encoding="utf-8")
        two.write_text("two", encoding="utf-8")
        archive = TDAArchive.create(self.root / "collision.tda")
        archive.add_paths([one])
        with self.assertRaises(ArchiveError):
            archive.add_paths([two])
        self.assertEqual(archive.read_preview("same.txt"), b"one")
        archive.close()

    def test_extract_refuses_overwrite(self) -> None:
        content = self.root / "note.txt"
        content.write_text("safe", encoding="utf-8")
        archive = TDAArchive.create(self.root / "safe.tda")
        archive.add_paths([content])
        destination = self.root / "out"
        destination.mkdir()
        (destination / "note.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(ArchiveError):
            archive.extract(destination)
        self.assertEqual((destination / "note.txt").read_text(encoding="utf-8"), "keep")
        archive.close()

    def test_extract_rejects_symlink_inside_destination(self) -> None:
        source_dir = self.root / "folder"
        source_dir.mkdir()
        (source_dir / "inside.txt").write_text("safe", encoding="utf-8")
        archive = TDAArchive.create(self.root / "symlink.tda")
        archive.add_paths([source_dir])
        destination = self.root / "out"
        destination.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        link = destination / "folder"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            archive.close()
            self.skipTest("Bu Windows ortamında sembolik bağlantı oluşturulamıyor.")
        with self.assertRaises(ArchiveError):
            archive.extract(destination)
        self.assertFalse((outside / "inside.txt").exists())
        archive.close()

    def test_create_refuses_overwrite_without_explicit_flag(self) -> None:
        path = self.root / "keep.tda"
        path.write_bytes(b"keep me")
        with self.assertRaises(ArchiveError):
            TDAArchive.create(path)
        self.assertEqual(path.read_bytes(), b"keep me")

    def test_unicode_normalization_collision_is_rejected(self) -> None:
        composed = self.root / "caf\u00e9.txt"
        decomposed = self.root / "cafe\u0301.txt"
        composed.write_text("one", encoding="utf-8")
        decomposed.write_text("two", encoding="utf-8")
        archive = TDAArchive.create(self.root / "unicode.tda")
        with self.assertRaises(ArchiveError):
            archive.add_paths([composed, decomposed])
        self.assertEqual(archive.file_count, 0)
        archive.close()

    def test_verify_wraps_runtime_zip_errors(self) -> None:
        source = self.root / "valid.txt"
        source.write_text("data", encoding="utf-8")
        archive = TDAArchive.create(self.root / "verify.tda")
        archive.add_paths([source])
        original_testzip = archive._zip.testzip
        archive._zip.testzip = lambda: (_ for _ in ()).throw(RuntimeError("simulated ZIP failure"))
        with self.assertRaises(ArchiveError):
            archive.verify()
        archive._zip.testzip = original_testzip
        archive.close()

    def test_manifest_hash_mismatch_is_rejected(self) -> None:
        source = self.root / "data.txt"
        source.write_text("original", encoding="utf-8")
        path = self.root / "tampered.tda"
        archive = TDAArchive.create(path)
        archive.add_paths([source])
        archive.close()

        replacement = self.root / "rewritten.zip"
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(replacement, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                data = src.read(info.filename)
                if info.filename == "content/data.txt":
                    data = b"modified"
                dst.writestr(info, data)
        replacement.replace(path)
        with self.assertRaises(ArchiveError):
            TDAArchive.open(path)

    def test_path_traversal_manifest_is_rejected(self) -> None:
        path = self.root / "unsafe.tda"
        manifest = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "entries": [{"path": "../escape.txt", "type": "file", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}],
        }
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("content/../escape.txt", b"x")
        with self.assertRaises(ArchiveError):
            TDAArchive.open(path)

    def test_encrypted_archive_roundtrip_and_wrong_password(self) -> None:
        source = self.root / "secret.txt"
        source.write_text("not stored as plaintext", encoding="utf-8")
        path = self.root / "encrypted.tda"
        archive = TDAArchive.create(path, "correct horse")
        archive.add_paths([source])
        archive.close()
        self.assertTrue(path.read_bytes().startswith(ENCRYPTED_MAGIC))
        self.assertNotIn(b"not stored as plaintext", path.read_bytes())
        with self.assertRaises(PasswordError):
            TDAArchive.open(path, "wrong password")
        opened = TDAArchive.open(path, "correct horse")
        self.assertTrue(opened.encrypted)
        self.assertEqual(opened.read_preview("secret.txt"), b"not stored as plaintext")
        opened.rename("secret.txt", "renamed.txt")
        opened.add_paths([source])
        opened.verify()
        opened.close()
        reopened = TDAArchive.open(path, "correct horse")
        self.assertEqual({e["path"] for e in reopened.entries if e["type"] == "file"}, {"secret.txt", "renamed.txt"})
        reopened.close()

    def test_encrypted_tamper_is_rejected(self) -> None:
        source = self.root / "note.txt"
        source.write_text("data", encoding="utf-8")
        path = self.root / "encrypted.tda"
        archive = TDAArchive.create(path, "pass")
        archive.add_paths([source])
        archive.close()
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 0x01
        path.write_bytes(raw)
        with self.assertRaises(PasswordError):
            TDAArchive.open(path, "pass")

    def test_non_archive_rejected_cleanly(self) -> None:
        path = self.root / "invalid.tda"
        path.write_bytes(b"not a zip")
        with self.assertRaises(ArchiveError):
            TDAArchive.open(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
