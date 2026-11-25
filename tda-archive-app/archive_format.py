"""TDA Archive 1.0 format and safe local archive operations.

Plain .tda files are ordinary ZIP files. Encrypted .tda files use the documented
TDAENC1 envelope and contain an AES-256-GCM encrypted ZIP payload.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import struct
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Mapping, Sequence

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    from cryptography.exceptions import InvalidTag
except ImportError:  # A clear error is raised only when encryption is requested/opened.
    Cipher = algorithms = modes = Scrypt = None  # type: ignore[assignment]
    InvalidTag = Exception  # type: ignore[assignment,misc]

FORMAT_NAME = "TDA Archive"
FORMAT_VERSION = "1.0"
MANIFEST_NAME = "manifest.json"
CONTENT_ROOT = "content/"
ENCRYPTED_MAGIC = b"TDAENC1\n"  # 8 bytes; followed by 16-byte salt and 12-byte nonce.
SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
KDF_N = 1 << 15
KDF_R = 8
KDF_P = 1
CHUNK_SIZE = 1024 * 1024
MAX_ENCRYPTED_FILE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_TOTAL_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 2 * 1024 * 1024 * 1024
_WINDOWS_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)
_INVALID_WIN_CHARS = set('<>:"|?*')


class ArchiveError(Exception):
    """Invalid archive, unsafe path, or failed archive operation."""


class PasswordError(ArchiveError):
    """Missing, incorrect, or unusable password for an encrypted archive."""


def _need_crypto() -> None:
    if Cipher is None or Scrypt is None:
        raise ArchiveError(
            "Şifreleme için 'cryptography' paketi gerekli. requirements.txt dosyasını kurun."
        )


def _derive_key(password: str, salt: bytes) -> bytes:
    _need_crypto()
    if not isinstance(password, str) or not password:
        raise PasswordError("Şifreli arşiv için parola gereklidir.")
    if len(password.encode("utf-8")) > 1024:
        raise PasswordError("Parola en fazla 1024 UTF-8 baytı olabilir.")
    try:
        return Scrypt(salt=salt, length=32, n=KDF_N, r=KDF_R, p=KDF_P).derive(
            password.encode("utf-8")
        )
    except Exception as exc:
        raise PasswordError("Parola anahtarı türetilemedi.") from exc


def _path_collision_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _safe_rel_path(value: str) -> str:
    """Validate a portable, relative Windows-safe archive path."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ArchiveError("Arşivde boş veya geçersiz bir yol bulundu.")
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ArchiveError(f"Güvensiz arşiv yolu reddedildi: {value!r}")
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise ArchiveError(f"Güvensiz arşiv yolu reddedildi: {value!r}")
    if p.as_posix() != value:
        raise ArchiveError(f"Kanonik olmayan arşiv yolu reddedildi: {value!r}")
    for part in p.parts:
        if part.endswith((".", " ")) or any(ord(ch) < 32 or ch in _INVALID_WIN_CHARS for ch in part):
            raise ArchiveError(f"Windows'ta kullanılamayan dosya adı reddedildi: {part!r}")
        if _WINDOWS_RESERVED.match(part):
            raise ArchiveError(f"Windows için ayrılmış dosya adı reddedildi: {part!r}")
    return p.as_posix()


def _sha256_stream(source: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


def _copy_stream(source: BinaryIO, target: BinaryIO, digest: "hashlib._Hash | None" = None) -> tuple[int, str | None]:
    h = digest or hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            break
        target.write(chunk)
        h.update(chunk)
        size += len(chunk)
    return size, h.hexdigest()


def _encrypt_file(source_path: Path, target_path: Path, password: str) -> None:
    _need_crypto()
    if source_path.stat().st_size + len(ENCRYPTED_MAGIC) + SALT_BYTES + NONCE_BYTES + TAG_BYTES > MAX_ENCRYPTED_FILE_BYTES:
        raise ArchiveError("Şifreli arşiv en fazla 256 MiB olabilir.")
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    header = ENCRYPTED_MAGIC + salt + nonce
    encryptor = Cipher(algorithms.AES(_derive_key(password, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    with source_path.open("rb") as source, target_path.open("wb") as target:
        target.write(header)
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            target.write(encryptor.update(chunk))
        target.write(encryptor.finalize())
        target.write(encryptor.tag)
        target.flush()
        os.fsync(target.fileno())


def _decrypt_bytes(source_path: Path, password: str) -> io.BytesIO:
    """Decrypt to memory (never a plaintext temporary file); bounded for safety."""
    _need_crypto()
    size = source_path.stat().st_size
    header_size = len(ENCRYPTED_MAGIC) + SALT_BYTES + NONCE_BYTES
    if size < header_size + TAG_BYTES:
        raise ArchiveError("Şifreli TDA dosyası eksik veya bozuk.")
    if size > MAX_ENCRYPTED_FILE_BYTES:
        raise ArchiveError("Şifreli arşiv 256 MiB sınırını aşıyor.")
    with source_path.open("rb") as source:
        header = source.read(header_size)
        if len(header) != header_size or not header.startswith(ENCRYPTED_MAGIC):
            raise ArchiveError("Geçersiz TDA şifreleme başlığı.")
        salt = header[len(ENCRYPTED_MAGIC):len(ENCRYPTED_MAGIC) + SALT_BYTES]
        nonce = header[-NONCE_BYTES:]
        source.seek(-TAG_BYTES, os.SEEK_END)
        tag = source.read(TAG_BYTES)
        ciphertext_len = size - header_size - TAG_BYTES
        source.seek(header_size)
        decryptor = Cipher(
            algorithms.AES(_derive_key(password, salt)), modes.GCM(nonce, tag)
        ).decryptor()
        decryptor.authenticate_additional_data(header)
        out = io.BytesIO()
        remaining = ciphertext_len
        try:
            while remaining:
                chunk = source.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ArchiveError("Şifreli arşiv beklenmedik biçimde sona erdi.")
                remaining -= len(chunk)
                out.write(decryptor.update(chunk))
            out.write(decryptor.finalize())
        except InvalidTag as exc:
            out.close()
            raise PasswordError("Parola yanlış veya arşivde oynama/bozulma var.") from exc
        except Exception:
            out.close()
            raise
        out.seek(0)
        return out


def _atomic_temp_path(directory: Path, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=".tda-", suffix=suffix, dir=str(directory))
    os.close(fd)
    return Path(name)


def _ensure_entry_tree(entries: Sequence[dict]) -> None:
    if len(entries) > MAX_ENTRIES:
        raise ArchiveError(f"Arşivde en fazla {MAX_ENTRIES:,} kayıt olabilir.")
    by_fold: dict[str, dict] = {}
    total_size = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ArchiveError("Manifestte geçersiz kayıt bulundu.")
        path = _safe_rel_path(entry.get("path"))
        kind = entry.get("type")
        if kind not in ("file", "dir"):
            raise ArchiveError(f"Geçersiz kayıt türü: {path}")
        key = _path_collision_key(path)
        if key in by_fold:
            raise ArchiveError(f"Büyük/küçük harf farkıyla çakışan yol bulundu: {path}")
        by_fold[key] = entry
        if kind == "file":
            size = entry.get("size")
            digest = entry.get("sha256")
            if not isinstance(size, int) or size < 0 or size > MAX_SINGLE_FILE_BYTES:
                raise ArchiveError(f"Geçersiz dosya boyutu: {path}")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ArchiveError(f"Geçersiz SHA-256 özeti: {path}")
            total_size += size
            if total_size > MAX_TOTAL_UNPACKED_BYTES:
                raise ArchiveError("Arşivin açılmış toplam boyutu güvenlik sınırını aşıyor.")
        elif entry.get("size", 0) not in (0, None):
            raise ArchiveError(f"Klasör kaydında boyut bulunamaz: {path}")
    for entry in entries:
        p = PurePosixPath(entry["path"])
        for parent in p.parents:
            if str(parent) == ".":
                continue
            parent_entry = by_fold.get(_path_collision_key(str(parent)))
            if parent_entry is None or parent_entry.get("type") != "dir" or parent_entry.get("path") != str(parent):
                raise ArchiveError(f"Üst klasör kaydı eksik, farklı yazılmış veya dosya: {parent}")
    # A file may not be the parent of another entry.
    for entry in entries:
        if entry["type"] != "file":
            continue
        prefix = _path_collision_key(entry["path"]) + "/"
        if any(k.startswith(prefix) for k in by_fold):
            raise ArchiveError(f"Dosya aynı zamanda klasör yolu olamaz: {entry['path']}")


def _read_manifest(zf: zipfile.ZipFile) -> tuple[list[dict], dict[str, zipfile.ZipInfo]]:
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES + 1:
        raise ArchiveError("Arşivde izin verilenden fazla kayıt var.")
    member_by_name: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if info.filename in member_by_name:
            raise ArchiveError(f"ZIP içinde yinelenen kayıt bulundu: {info.filename}")
        if info.file_size < 0 or info.file_size > MAX_SINGLE_FILE_BYTES:
            raise ArchiveError(f"ZIP kaydı güvenlik boyut sınırını aşıyor: {info.filename}")
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK:
            raise ArchiveError("Sembolik bağlantı içeren arşivler desteklenmez.")
        if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ArchiveError("Özel dosya türü içeren arşivler desteklenmez.")
        member_by_name[info.filename] = info
    m_info = member_by_name.get(MANIFEST_NAME)
    if m_info is None or m_info.is_dir() or m_info.file_size > MAX_MANIFEST_BYTES:
        raise ArchiveError("TDA manifesti yok veya geçersiz.")
    try:
        raw = zf.read(m_info)
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ArchiveError("TDA manifesti okunamadı; dosya bozuk olabilir.") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME or manifest.get("version") != FORMAT_VERSION:
        raise ArchiveError("Dosya TDA Archive 1.0 biçiminde değil.")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ArchiveError("Manifest kayıt listesi geçersiz.")
    _ensure_entry_tree(entries)
    expected: dict[str, dict] = {}
    for entry in entries:
        path = entry["path"]
        member_name = CONTENT_ROOT + path + ("/" if entry["type"] == "dir" else "")
        if member_name in expected:
            raise ArchiveError(f"Manifestte yinelenen yol: {path}")
        expected[member_name] = entry
    actual_names = set(member_by_name) - {MANIFEST_NAME}
    if actual_names != set(expected):
        raise ArchiveError("ZIP içeriği ile manifest kayıtları uyuşmuyor.")
    for name, entry in expected.items():
        info = member_by_name[name]
        if (entry["type"] == "dir") != info.is_dir():
            raise ArchiveError(f"Kayıt türü manifestle uyuşmuyor: {entry['path']}")
        if entry["type"] == "file" and info.file_size != entry["size"]:
            raise ArchiveError(f"Dosya boyutu manifestle uyuşmuyor: {entry['path']}")
    total_zip_size = sum(e["size"] for e in entries if e["type"] == "file")
    if total_zip_size > MAX_TOTAL_UNPACKED_BYTES:
        raise ArchiveError("Arşivin açılmış toplam boyutu güvenlik sınırını aşıyor.")
    return entries, member_by_name


class TDAArchive:
    """Opened TDA archive. Encrypted ZIP plaintext is kept only in memory."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.password: str | None = None  # Kept in this process only; never serialized.
        self.encrypted = False
        self.entries: list[dict] = []
        self._zip: zipfile.ZipFile | None = None
        self._source: BinaryIO | None = None

    @classmethod
    def create(
        cls, path: str | Path, password: str | None = None, *, overwrite: bool = False
    ) -> "TDAArchive":
        target = Path(path).expanduser().resolve()
        if target.suffix.lower() != ".tda":
            raise ArchiveError("Yeni arşiv dosyasının uzantısı .tda olmalıdır.")
        if target.exists() and not overwrite:
            raise ArchiveError("Dosya zaten var; üzerine yazmak için açıkça onay verin.")
        if password is not None and not password:
            password = None
        if password:
            _need_crypto()
        target.parent.mkdir(parents=True, exist_ok=True)
        zip_temp: Path | None = _atomic_temp_path(target.parent, ".zip.tmp")
        final_temp: Path | None = None
        try:
            assert zip_temp is not None
            with zipfile.ZipFile(zip_temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                manifest = cls._manifest_bytes([])
                zf.writestr(MANIFEST_NAME, manifest)
            if password:
                final_temp = _atomic_temp_path(target.parent, ".tda.tmp")
                _encrypt_file(zip_temp, final_temp, password)
                os.replace(final_temp, target)
                final_temp = None
            else:
                os.replace(zip_temp, target)
                zip_temp = None
            return cls.open(target, password)
        except Exception:
            raise
        finally:
            if zip_temp is not None and zip_temp.exists():
                zip_temp.unlink(missing_ok=True)
            if final_temp and final_temp.exists():
                final_temp.unlink(missing_ok=True)

    @classmethod
    def open(cls, path: str | Path, password: str | None = None) -> "TDAArchive":
        target = Path(path).expanduser().resolve()
        if not target.is_file():
            raise ArchiveError("TDA dosyası bulunamadı.")
        obj = cls()
        obj.path = target
        obj.password = password
        try:
            with target.open("rb") as probe:
                signature = probe.read(len(ENCRYPTED_MAGIC))
            obj.encrypted = signature == ENCRYPTED_MAGIC
            if obj.encrypted:
                if not password:
                    raise PasswordError("Bu arşiv parola korumalıdır.")
                obj._source = _decrypt_bytes(target, password)
                obj._zip = zipfile.ZipFile(obj._source, "r")
            else:
                obj._zip = zipfile.ZipFile(target, "r")
            entries, _ = _read_manifest(obj._zip)
            obj.entries = [dict(item) for item in entries]
            obj.verify()
            return obj
        except ArchiveError:
            obj.close()
            raise
        except Exception as exc:
            obj.close()
            raise ArchiveError("Geçersiz veya bozuk TDA arşivi; dosya güvenle açılmadı.") from exc

    @staticmethod
    def _manifest_bytes(entries: Sequence[dict]) -> bytes:
        body = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entries": list(entries),
        }
        return (json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    def close(self) -> None:
        if self._zip is not None:
            try:
                self._zip.close()
            finally:
                self._zip = None
        if self._source is not None:
            try:
                self._source.close()
            finally:
                self._source = None
        self.password = None

    def __enter__(self) -> "TDAArchive":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _require_open(self) -> zipfile.ZipFile:
        if self._zip is None or self.path is None:
            raise ArchiveError("Önce bir TDA arşivi açın.")
        return self._zip

    @property
    def total_size(self) -> int:
        return sum(e["size"] for e in self.entries if e["type"] == "file")

    @property
    def file_count(self) -> int:
        return sum(e["type"] == "file" for e in self.entries)

    @property
    def folder_count(self) -> int:
        return sum(e["type"] == "dir" for e in self.entries)

    def verify(self) -> tuple[int, int]:
        zf = self._require_open()
        try:
            entries, members = _read_manifest(zf)
            damaged_member = zf.testzip()
        except ArchiveError:
            raise
        except Exception as exc:
            raise ArchiveError("ZIP CRC/bütünlük kontrolü sırasında arşiv okunamadı.") from exc
        if damaged_member is not None:
            raise ArchiveError("ZIP CRC bütünlük kontrolü başarısız oldu.")
        count = 0
        for entry in entries:
            if entry["type"] != "file":
                continue
            digest = hashlib.sha256()
            size = 0
            try:
                with zf.open(members[CONTENT_ROOT + entry["path"]], "r") as stream:
                    while True:
                        block = stream.read(CHUNK_SIZE)
                        if not block:
                            break
                        digest.update(block)
                        size += len(block)
            except Exception as exc:
                raise ArchiveError(f"Dosya okunamadı: {entry['path']}") from exc
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise ArchiveError(f"SHA-256 bütünlük kontrolü başarısız: {entry['path']}")
            count += 1
        return count, self.total_size

    def _add_parent_dirs(self, entries: list[dict]) -> list[dict]:
        by_path: dict[str, dict] = {}
        by_fold: set[str] = set()
        for entry in entries:
            path = _safe_rel_path(entry.get("path"))
            key = _path_collision_key(path)
            if key in by_fold:
                raise ArchiveError(f"Arşivde yinelenen veya çakışan yol: {path}")
            by_fold.add(key)
            by_path[path] = dict(entry)
        for entry in list(by_path.values()):
            p = PurePosixPath(entry["path"])
            for parent in reversed(p.parents):
                parent_name = str(parent)
                if parent_name == ".":
                    continue
                existing = by_path.get(parent_name)
                if existing and existing["type"] != "dir":
                    raise ArchiveError(f"Üst yol dosyadır: {parent_name}")
                if not existing:
                    by_path[parent_name] = {"path": parent_name, "type": "dir", "size": 0}
        result = list(by_path.values())
        _ensure_entry_tree(result)
        return result

    def _rewrite(
        self,
        new_entries: list[dict],
        old_sources: Mapping[str, str],
        new_sources: Mapping[str, Path],
    ) -> None:
        zf = self._require_open()
        assert self.path is not None
        password = self.password
        entries = self._add_parent_dirs(new_entries)
        target = self.path
        parent = target.parent
        zip_temp: Path | None = _atomic_temp_path(parent, ".zip.tmp")
        final_temp: Path | None = None
        try:
            assert zip_temp is not None
            output_entries: list[dict] = []
            with zipfile.ZipFile(zip_temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as out:
                for item in sorted(entries, key=lambda e: e["path"].casefold()):
                    path = item["path"]
                    if item["type"] == "dir":
                        info = zipfile.ZipInfo(CONTENT_ROOT + path + "/")
                        info.compress_type = zipfile.ZIP_STORED
                        info.external_attr = (stat.S_IFDIR | 0o755) << 16
                        out.writestr(info, b"")
                        output_entries.append({"path": path, "type": "dir", "size": 0})
                        continue
                    info = zipfile.ZipInfo(CONTENT_ROOT + path)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    digest = hashlib.sha256()
                    size = 0
                    if path in new_sources:
                        with new_sources[path].open("rb") as source, out.open(info, "w") as dest:
                            size, _ = _copy_stream(source, dest, digest)
                    else:
                        old_path = old_sources.get(path, path)
                        old_member = CONTENT_ROOT + old_path
                        try:
                            with zf.open(old_member, "r") as source, out.open(info, "w") as dest:
                                size, _ = _copy_stream(source, dest, digest)
                        except KeyError as exc:
                            raise ArchiveError(f"Arşiv kaydı bulunamadı: {old_path}") from exc
                    if size > MAX_SINGLE_FILE_BYTES:
                        raise ArchiveError(f"Dosya izin verilen boyutu aşıyor: {path}")
                    output_entries.append({
                        "path": path, "type": "file", "size": size, "sha256": digest.hexdigest()
                    })
                _ensure_entry_tree(output_entries)
                out.writestr(MANIFEST_NAME, self._manifest_bytes(output_entries))
            final_candidate = zip_temp
            if password:
                final_temp = _atomic_temp_path(parent, ".tda.tmp")
                _encrypt_file(zip_temp, final_temp, password)
                final_candidate = final_temp
            # Close handles before replacing the file; required on Windows.
            self.close()
            try:
                os.replace(final_candidate, target)
            except Exception:
                # Keep the old archive open if the atomic replacement did not happen.
                try:
                    reopened = TDAArchive.open(target, password)
                    self._adopt(reopened)
                except Exception:
                    pass
                raise
            if final_candidate == zip_temp:
                zip_temp = None
            else:
                final_temp = None
            reopened = TDAArchive.open(target, password)
            self._adopt(reopened)
        finally:
            if zip_temp is not None and zip_temp.exists():
                zip_temp.unlink(missing_ok=True)
            if final_temp and final_temp.exists():
                final_temp.unlink(missing_ok=True)

    def _adopt(self, other: "TDAArchive") -> None:
        self.close()
        self.path = other.path
        self.password = other.password
        self.encrypted = other.encrypted
        self.entries = other.entries
        self._zip = other._zip
        self._source = other._source
        other._zip = None
        other._source = None

    def _collect_sources(self, paths: Iterable[str | Path]) -> tuple[list[dict], dict[str, Path]]:
        items: dict[str, dict] = {}
        source_map: dict[str, Path] = {}

        def add_dir(rel: str) -> None:
            rel = _safe_rel_path(rel)
            prior = items.get(rel)
            if prior and prior["type"] != "dir":
                raise ArchiveError(f"Dosya/klasör adı çakışması: {rel}")
            items[rel] = {"path": rel, "type": "dir", "size": 0}

        for supplied in paths:
            raw_src = Path(supplied).expanduser()
            if raw_src.is_symlink():
                raise ArchiveError(f"Sembolik bağlantı desteklenmiyor: {raw_src}")
            src = raw_src.resolve()
            if not src.exists():
                raise ArchiveError(f"Dosya bulunamadı veya sembolik bağlantı desteklenmiyor: {src}")
            if src.is_file():
                rel = _safe_rel_path(src.name)
                if rel in items:
                    raise ArchiveError(f"Birden fazla kaynak aynı dosya adına sahip: {rel}")
                items[rel] = {"path": rel, "type": "file", "size": 0, "sha256": "0" * 64}
                source_map[rel] = src
            elif src.is_dir():
                base = _safe_rel_path(src.name)
                add_dir(base)
                for root, dirnames, filenames in os.walk(src, followlinks=False):
                    root_path = Path(root)
                    for name in list(dirnames):
                        child = root_path / name
                        if child.is_symlink():
                            raise ArchiveError(f"Sembolik bağlantı desteklenmiyor: {child}")
                    for name in filenames:
                        child = root_path / name
                        if child.is_symlink() or not child.is_file():
                            raise ArchiveError(f"Sembolik bağlantı/desteklenmeyen kaynak: {child}")
                    rel_dir = base if root_path == src else base + "/" + root_path.relative_to(src).as_posix()
                    if root_path != src:
                        add_dir(rel_dir)
                    for name in filenames:
                        rel = _safe_rel_path(rel_dir + "/" + name)
                        if rel in items:
                            raise ArchiveError(f"Yinelenen kaynak yolu: {rel}")
                        items[rel] = {"path": rel, "type": "file", "size": 0, "sha256": "0" * 64}
                        source_map[rel] = root_path / name
            else:
                raise ArchiveError(f"Dosya türü desteklenmiyor: {src}")
        return list(items.values()), source_map

    def add_paths(self, paths: Iterable[str | Path]) -> None:
        additions, source_map = self._collect_sources(paths)
        existing = [dict(e) for e in self.entries]
        all_entries = existing + additions
        self._rewrite(all_entries, {e["path"]: e["path"] for e in self.entries}, source_map)

    def delete(self, path: str) -> None:
        path = _safe_rel_path(path)
        chosen = next((e for e in self.entries if e["path"] == path), None)
        if not chosen:
            raise ArchiveError(f"Kayıt bulunamadı: {path}")
        remove = {path}
        if chosen["type"] == "dir":
            remove.update(e["path"] for e in self.entries if e["path"].startswith(path + "/"))
        remain = [dict(e) for e in self.entries if e["path"] not in remove]
        self._rewrite(remain, {e["path"]: e["path"] for e in remain}, {})

    def rename(self, old_path: str, new_path: str) -> None:
        old_path = _safe_rel_path(old_path)
        new_path = _safe_rel_path(new_path)
        selected = next((e for e in self.entries if e["path"] == old_path), None)
        if not selected:
            raise ArchiveError(f"Kayıt bulunamadı: {old_path}")
        if old_path == new_path:
            return
        rename_map: dict[str, str] = {}
        for entry in self.entries:
            p = entry["path"]
            if p == old_path or (selected["type"] == "dir" and p.startswith(old_path + "/")):
                rename_map[p] = new_path + p[len(old_path):]
        remain = [dict(e) for e in self.entries if e["path"] not in rename_map]
        moved: list[dict] = []
        old_sources: dict[str, str] = {e["path"]: e["path"] for e in remain}
        for entry in self.entries:
            if entry["path"] in rename_map:
                clone = dict(entry)
                clone["path"] = rename_map[entry["path"]]
                moved.append(clone)
                old_sources[clone["path"]] = entry["path"]
        self._rewrite(remain + moved, old_sources, {})

    def read_preview(self, path: str, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        path = _safe_rel_path(path)
        item = next((e for e in self.entries if e["path"] == path and e["type"] == "file"), None)
        if not item:
            raise ArchiveError("Önizlenecek dosya bulunamadı.")
        if item["size"] > max_bytes:
            raise ArchiveError("Dosya önizleme için çok büyük.")
        try:
            return self._require_open().read(CONTENT_ROOT + path)
        except (KeyError, zipfile.BadZipFile, OSError) as exc:
            raise ArchiveError("Dosya önizlemesi okunamadı.") from exc

    def extract(self, destination: str | Path, selected_paths: Iterable[str] | None = None) -> list[Path]:
        zf = self._require_open()
        raw_root = Path(destination).expanduser()
        if raw_root.is_symlink():
            raise ArchiveError("Sembolik bağlantı olan çıkarma konumu kabul edilmez.")
        root = raw_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise ArchiveError("Çıkarma konumu güvenli bir klasör değil.")
        root_real = root.resolve(strict=True)
        by_path = {e["path"]: e for e in self.entries}
        if selected_paths is None:
            selected = set(by_path)
        else:
            selected = set()
            for raw in selected_paths:
                rel = _safe_rel_path(raw)
                item = by_path.get(rel)
                if not item:
                    raise ArchiveError(f"Arşiv kaydı bulunamadı: {rel}")
                selected.add(rel)
                if item["type"] == "dir":
                    selected.update(p for p in by_path if p.startswith(rel + "/"))
        for rel in list(selected):
            for parent in PurePosixPath(rel).parents:
                if str(parent) != "." and str(parent) in by_path:
                    selected.add(str(parent))
        created: list[Path] = []
        try:
            for rel in sorted(selected, key=lambda p: (len(PurePosixPath(p).parts), p.casefold())):
                item = by_path[rel]
                target = root.joinpath(*PurePosixPath(rel).parts)
                current = root
                for part in PurePosixPath(rel).parts[:-1] if item["type"] == "file" else PurePosixPath(rel).parts:
                    current = current / part
                    if current.exists() or current.is_symlink():
                        if current.is_symlink() or not current.is_dir():
                            raise ArchiveError(f"Güvensiz hedef klasörü reddedildi: {current}")
                    else:
                        current.mkdir()
                        created.append(current)
                    if os.path.commonpath([str(root_real), str(current.resolve())]) != str(root_real):
                        raise ArchiveError("Çıkarma yolu hedef klasörün dışına çıkıyor.")
                if item["type"] == "dir":
                    continue
                if target.exists() or target.is_symlink():
                    raise ArchiveError(f"Mevcut dosyanın üzerine yazılmadı: {target}")
                if os.path.commonpath([str(root_real), str(target.parent.resolve())]) != str(root_real):
                    raise ArchiveError("Çıkarma yolu hedef klasörün dışına çıkıyor.")
                digest = hashlib.sha256()
                count = 0
                try:
                    with zf.open(CONTENT_ROOT + rel, "r") as source, target.open("xb") as dest:
                        while True:
                            chunk = source.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            dest.write(chunk)
                            digest.update(chunk)
                            count += len(chunk)
                    if count != item["size"] or digest.hexdigest() != item["sha256"]:
                        raise ArchiveError(f"Çıkarılan dosya bütünlük kontrolünden geçmedi: {rel}")
                    created.append(target)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
            return created
        except Exception:
            # Leave completed outputs in place; remove only an incomplete current file.
            raise


def human_size(n: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"
