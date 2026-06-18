from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core.runtime_paths import data_path

try:
    from nacl import encoding, signing  # type: ignore

    _SIGNER_BACKEND = "pynacl"
except ImportError:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

    _SIGNER_BACKEND = "cryptography"


_KEY_DIR = data_path("keys")
_LEGACY_PRIV_KEY_PATH = _KEY_DIR / "node_signing_key.b64"
_KEY_RECORD_PATH = _KEY_DIR / "node_signing_key.json"
_KEYRING_RECORD_PATH = _KEY_DIR / "node_signing_key.keyring.json"
_KEY_ARCHIVE_DIR = _KEY_DIR / "archive"
_KEY_RECORD_VERSION = 1
_KEY_PASSPHRASE_ENV = "NULLA_KEY_PASSPHRASE"
_KEY_STORAGE_MODE_ENV = "NULLA_KEY_STORAGE_MODE"
_PBKDF2_ITERATIONS = 390_000
_KEYRING_SERVICE = "nulla"
_KEYRING_ACCOUNT = "node_signing_key"
_LOCAL_KEYPAIR: LocalKeypair | None = None


@dataclass
class LocalKeypair:
    signing_key: object
    verify_key: object

    @property
    def peer_id(self) -> str:
        if _SIGNER_BACKEND == "pynacl":
            return self.verify_key.encode(encoder=encoding.HexEncoder).decode("utf-8")
        raw = self.verify_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return raw.hex()


@dataclass(frozen=True)
class KeyStorageMetadata:
    format: str
    path: Path


def _ensure_dir() -> None:
    _KEY_DIR.mkdir(parents=True, exist_ok=True)
    _KEY_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    _chmod_safe(_KEY_DIR, 0o700)
    _chmod_safe(_KEY_ARCHIVE_DIR, 0o700)


def _chmod_safe(path: Path, mode: int) -> None:
    try:
        if os.name == "posix":
            path.chmod(mode)
    except Exception:
        return


def _enforce_private_key_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        st_mode = path.stat().st_mode & 0o777
        if st_mode != 0o600:
            path.chmod(0o600)
    except Exception:
        return


def _generate_signing_key():
    if _SIGNER_BACKEND == "pynacl":
        return signing.SigningKey.generate()
    return Ed25519PrivateKey.generate()


def _signing_key_from_seed(seed: bytes):
    if _SIGNER_BACKEND == "pynacl":
        return signing.SigningKey(seed)
    return Ed25519PrivateKey.from_private_bytes(seed)


def _signing_key_bytes(signing_key: object) -> bytes:
    if _SIGNER_BACKEND == "pynacl":
        return bytes(signing_key)
    return signing_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _verify_key(signing_key: object):
    if _SIGNER_BACKEND == "pynacl":
        return signing_key.verify_key
    return signing_key.public_key()


def _key_passphrase() -> str | None:
    raw = str(os.environ.get(_KEY_PASSPHRASE_ENV, "") or "").strip()
    return raw or None


def _key_storage_preference() -> str:
    raw = str(os.environ.get(_KEY_STORAGE_MODE_ENV, "") or "").strip().lower()
    if raw in {"auto", "file", "keyring"}:
        return raw
    return "auto"


def _keyring_backend():
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    return keyring


def _peer_id_for_seed(seed: bytes) -> str:
    signing_key = _signing_key_from_seed(seed)
    return LocalKeypair(signing_key=signing_key, verify_key=_verify_key(signing_key)).peer_id


def _keyring_record_payload(*, service: str, account: str, peer_id: str) -> dict[str, object]:
    return {
        "version": _KEY_RECORD_VERSION,
        "format": "keyring_seed",
        "service": service,
        "account": account,
        "peer_id": peer_id,
    }


def _write_keyring_record(seed: bytes, *, service: str = _KEYRING_SERVICE, account: str = _KEYRING_ACCOUNT) -> None:
    backend = _keyring_backend()
    if backend is None:
        raise RuntimeError("Keyring storage requested but no keyring backend is available.")
    backend.set_password(service, account, base64.b64encode(seed).decode("utf-8"))
    _KEYRING_RECORD_PATH.write_text(
        json.dumps(
            _keyring_record_payload(service=service, account=account, peer_id=_peer_id_for_seed(seed)),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _chmod_safe(_KEYRING_RECORD_PATH, 0o600)


def _load_keyring_seed(path: Path) -> bytes:
    backend = _keyring_backend()
    if backend is None:
        raise RuntimeError("Keyring signing key record exists but no keyring backend is available.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("format") or "") != "keyring_seed":
        raise ValueError(f"Unsupported signing key record format: {payload.get('format')!r}")
    service = str(payload.get("service") or _KEYRING_SERVICE).strip() or _KEYRING_SERVICE
    account = str(payload.get("account") or _KEYRING_ACCOUNT).strip() or _KEYRING_ACCOUNT
    encoded = backend.get_password(service, account)
    if not encoded:
        raise RuntimeError(f"Keyring signing key payload is missing for {service}:{account}.")
    return base64.b64decode(encoded)


def _delete_keyring_record(path: Path) -> None:
    if not path.exists():
        return
    backend = _keyring_backend()
    if backend is None:
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        service = str(payload.get("service") or _KEYRING_SERVICE).strip() or _KEYRING_SERVICE
        account = str(payload.get("account") or _KEYRING_ACCOUNT).strip() or _KEYRING_ACCOUNT
        backend.delete_password(service, account)
    except Exception:
        return


def _derive_encryption_key(*, passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _hkdf_sha256(ikm: bytes, *, salt: bytes, info: bytes, length: int) -> bytes:
    if length < 1:
        raise ValueError("length must be positive")
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    block = b""
    output = b""
    for counter in range(1, -(-length // 32) + 1):
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
    return output[:length]


def derive_local_secret(label: str | bytes, *, length: int = 32) -> bytes:
    """Derive scoped local secret material from the node signing key.

    Callers get a label-scoped secret, not the raw node signing seed. That keeps
    wallet/tool encryption coupled to NULLA identity without spreading key bytes.
    """
    normalized_label = label.encode("utf-8") if isinstance(label, str) else bytes(label)
    if not normalized_label:
        raise ValueError("label must not be empty")
    seed = _signing_key_bytes(load_or_create_local_keypair().signing_key)
    return _hkdf_sha256(
        seed,
        salt=b"nulla-local-secret-v1",
        info=normalized_label,
        length=length,
    )


def _encrypted_record_payload(seed: bytes, *, passphrase: str) -> dict[str, object]:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_encryption_key(passphrase=passphrase, salt=salt)
    ciphertext = AESGCM(key).encrypt(nonce, seed, b"nulla-node-signing-key")
    return {
        "version": _KEY_RECORD_VERSION,
        "format": "encrypted_seed",
        "kdf": "pbkdf2_sha256",
        "iterations": _PBKDF2_ITERATIONS,
        "salt_b64": base64.b64encode(salt).decode("utf-8"),
        "nonce_b64": base64.b64encode(nonce).decode("utf-8"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("utf-8"),
    }


def _write_encrypted_key_record(seed: bytes, *, passphrase: str) -> None:
    _KEY_RECORD_PATH.write_text(
        json.dumps(_encrypted_record_payload(seed, passphrase=passphrase), sort_keys=True),
        encoding="utf-8",
    )
    _chmod_safe(_KEY_RECORD_PATH, 0o600)


def _load_encrypted_seed(path: Path, *, passphrase: str) -> bytes:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("format") or "") != "encrypted_seed":
        raise ValueError(f"Unsupported signing key record format: {payload.get('format')!r}")
    salt = base64.b64decode(str(payload.get("salt_b64") or ""))
    nonce = base64.b64decode(str(payload.get("nonce_b64") or ""))
    ciphertext = base64.b64decode(str(payload.get("ciphertext_b64") or ""))
    key = _derive_encryption_key(passphrase=passphrase, salt=salt)
    return AESGCM(key).decrypt(nonce, ciphertext, b"nulla-node-signing-key")


def _storage_metadata() -> KeyStorageMetadata:
    if _KEYRING_RECORD_PATH.exists():
        return KeyStorageMetadata(format="keyring_seed", path=_KEYRING_RECORD_PATH)
    if _KEY_RECORD_PATH.exists():
        return KeyStorageMetadata(format="encrypted_seed", path=_KEY_RECORD_PATH)
    return KeyStorageMetadata(format="legacy_plaintext_seed", path=_LEGACY_PRIV_KEY_PATH)


def _archive_keyring_seed(seed: bytes, *, current_peer_id: str) -> Path:
    _ensure_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"{timestamp}-{current_peer_id[:24]}.json"
    archive_account = f"{_KEYRING_ACCOUNT}.archive.{timestamp}.{current_peer_id[:24]}"
    archived_path = _KEY_ARCHIVE_DIR / archive_name
    _write_keyring_record(seed, service=_KEYRING_SERVICE, account=archive_account)
    archived_path.write_text(
        json.dumps(
            _keyring_record_payload(service=_KEYRING_SERVICE, account=archive_account, peer_id=current_peer_id),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _chmod_safe(archived_path, 0o600)
    if _KEYRING_RECORD_PATH.exists():
        _KEYRING_RECORD_PATH.unlink()
    return archived_path


def _archive_current_key_material() -> Path:
    _ensure_dir()
    current_peer_id = get_local_peer_id()
    metadata = _storage_metadata()
    if metadata.format == "keyring_seed":
        return _archive_keyring_seed(_load_keyring_seed(metadata.path), current_peer_id=current_peer_id)
    suffix = ".json" if metadata.format == "encrypted_seed" else ".b64"
    archive_name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{current_peer_id[:24]}{suffix}"
    archived_path = _KEY_ARCHIVE_DIR / archive_name
    archived_path.write_text(metadata.path.read_text(encoding="utf-8"), encoding="utf-8")
    _chmod_safe(archived_path, 0o600)
    return archived_path


def _persist_seed(seed: bytes) -> KeyStorageMetadata:
    _ensure_dir()
    if _key_storage_preference() == "keyring":
        _write_keyring_record(seed)
        if _LEGACY_PRIV_KEY_PATH.exists():
            _LEGACY_PRIV_KEY_PATH.unlink()
        if _KEY_RECORD_PATH.exists():
            _KEY_RECORD_PATH.unlink()
        return KeyStorageMetadata(format="keyring_seed", path=_KEYRING_RECORD_PATH)
    passphrase = _key_passphrase()
    if passphrase:
        _write_encrypted_key_record(seed, passphrase=passphrase)
        if _LEGACY_PRIV_KEY_PATH.exists():
            _LEGACY_PRIV_KEY_PATH.unlink()
        if _KEYRING_RECORD_PATH.exists():
            _delete_keyring_record(_KEYRING_RECORD_PATH)
            _KEYRING_RECORD_PATH.unlink()
        return KeyStorageMetadata(format="encrypted_seed", path=_KEY_RECORD_PATH)
    _LEGACY_PRIV_KEY_PATH.write_text(base64.b64encode(seed).decode("utf-8"), encoding="utf-8")
    _chmod_safe(_LEGACY_PRIV_KEY_PATH, 0o600)
    if _KEY_RECORD_PATH.exists():
        _KEY_RECORD_PATH.unlink()
    if _KEYRING_RECORD_PATH.exists():
        _delete_keyring_record(_KEYRING_RECORD_PATH)
        _KEYRING_RECORD_PATH.unlink()
    return KeyStorageMetadata(format="legacy_plaintext_seed", path=_LEGACY_PRIV_KEY_PATH)


def key_storage_mode() -> str:
    if _KEYRING_RECORD_PATH.exists():
        return "keyring"
    if _KEY_RECORD_PATH.exists():
        return "encrypted_file"
    return "legacy_plaintext_file"


def load_or_create_local_keypair() -> LocalKeypair:
    global _LOCAL_KEYPAIR
    if _LOCAL_KEYPAIR is not None:
        return _LOCAL_KEYPAIR

    _ensure_dir()

    if _KEYRING_RECORD_PATH.exists():
        _enforce_private_key_permissions(_KEYRING_RECORD_PATH)
        seed = _load_keyring_seed(_KEYRING_RECORD_PATH)
        sk = _signing_key_from_seed(seed)
        _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
        return _LOCAL_KEYPAIR

    if _KEY_RECORD_PATH.exists():
        _enforce_private_key_permissions(_KEY_RECORD_PATH)
        passphrase = _key_passphrase()
        if not passphrase:
            raise RuntimeError(
                f"Encrypted signing key record exists at {_KEY_RECORD_PATH} but {_KEY_PASSPHRASE_ENV} is not set."
            )
        seed = _load_encrypted_seed(_KEY_RECORD_PATH, passphrase=passphrase)
        sk = _signing_key_from_seed(seed)
        _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
        return _LOCAL_KEYPAIR

    if _LEGACY_PRIV_KEY_PATH.exists():
        _enforce_private_key_permissions(_LEGACY_PRIV_KEY_PATH)
        raw = _LEGACY_PRIV_KEY_PATH.read_text(encoding="utf-8").strip()
        seed = base64.b64decode(raw)
        if _key_passphrase():
            _persist_seed(seed)
        sk = _signing_key_from_seed(seed)
        _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
        return _LOCAL_KEYPAIR

    sk = _generate_signing_key()
    _persist_seed(_signing_key_bytes(sk))
    _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
    return _LOCAL_KEYPAIR


def sign(payload_bytes: bytes) -> str:
    kp = load_or_create_local_keypair()
    if _SIGNER_BACKEND == "pynacl":
        signed = kp.signing_key.sign(payload_bytes)
        return base64.b64encode(signed.signature).decode("utf-8")
    signature = kp.signing_key.sign(payload_bytes)
    return base64.b64encode(signature).decode("utf-8")


def verify(payload_bytes: bytes, signature: str, peer_id: str) -> bool:
    try:
        sig = base64.b64decode(signature)
        if _SIGNER_BACKEND == "pynacl":
            verify_key = signing.VerifyKey(peer_id, encoder=encoding.HexEncoder)
            verify_key.verify(payload_bytes, sig)
            return True
        verify_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(peer_id))
        verify_key.verify(sig, payload_bytes)
        return True
    except Exception:
        if _SIGNER_BACKEND == "pynacl":
            return False
        return False


def get_local_peer_id() -> str:
    return load_or_create_local_keypair().peer_id


def local_key_path() -> Path:
    _ensure_dir()
    return _storage_metadata().path


def rotate_local_keypair() -> dict[str, object]:
    global _LOCAL_KEYPAIR
    existing_peer_id = get_local_peer_id()
    archived_path = _archive_current_key_material()
    sk = _generate_signing_key()
    _persist_seed(_signing_key_bytes(sk))
    _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
    return {
        "old_peer_id": existing_peer_id,
        "new_peer_id": _LOCAL_KEYPAIR.peer_id,
        "archived_key_path": archived_path,
    }
