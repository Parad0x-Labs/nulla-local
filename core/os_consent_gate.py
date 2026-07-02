"""OS-native user-consent gate for sensitive wallet actions.

The agent's Solana private seed is encrypted at rest and used transiently for
signing. Revealing the raw key to a human (for backup) is a different, higher-bar
action: it must require a live, OS-level confirmation so that neither OpenClaw nor
the agent process can surface the key silently.

Design goals:
  * Fail CLOSED. If no OS consent mechanism is available, or on a platform we do
    not yet support, consent is DENIED - never silently granted.
  * Prefer real verification (Windows Hello PIN/fingerprint/face via the WinRT
    UserConsentVerifier) when it is configured and the `winsdk`/`winrt` bridge is
    importable. That actually verifies the user, not just their presence.
  * Fall back to a native interactive credential prompt (CredUIPromptForWindows-
    CredentialsW) which forces a human to actively confirm at the keyboard. This
    is a PRESENCE gate, weaker than Hello - documented as such, not oversold.
  * Be fully unit-testable without a live prompt via `set_consent_override_for_tests`.

A single explicit escape hatch exists for headless/CI: NULLA_WALLET_SKIP_CONSENT_GATE=yes.
It requires the literal value "yes", logs a warning every time, and must never be
set in a normal user install.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable

logger = logging.getLogger("nulla.consent")

_SKIP_ENV = "NULLA_WALLET_SKIP_CONSENT_GATE"

# Test-only injection point. When set, it fully replaces the native path so unit
# tests can simulate grant/deny without a real OS prompt.
_TEST_OVERRIDE: Callable[[str], bool] | None = None


class ConsentDenied(Exception):
    """The user was prompted and declined / failed verification."""


class ConsentUnavailable(Exception):
    """No OS consent mechanism is available on this platform/config (fail closed)."""


def set_consent_override_for_tests(fn: Callable[[str], bool] | None) -> None:
    """Install (or clear with None) a test hook that stands in for the OS prompt."""
    global _TEST_OVERRIDE
    _TEST_OVERRIDE = fn


def _env_bypass_enabled() -> bool:
    return str(os.environ.get(_SKIP_ENV, "")).strip().lower() == "yes"


def require_os_user_consent(reason: str) -> bool:
    """Return True only if the OS confirmed a live user consent for `reason`.

    Raises ConsentDenied if the user declined, ConsentUnavailable if no mechanism
    exists. Callers gating a key reveal should treat any exception as "do not reveal".
    """
    clean_reason = str(reason or "confirm this sensitive action").strip()

    if _TEST_OVERRIDE is not None:
        return bool(_TEST_OVERRIDE(clean_reason))

    if _env_bypass_enabled():
        logger.warning(
            "OS consent gate BYPASSED via %s=yes for: %s. This must never be set on a real install.",
            _SKIP_ENV,
            clean_reason,
        )
        return True

    if sys.platform != "win32":
        # macOS (LocalAuthentication) and Linux (polkit/PAM) are not wired yet.
        raise ConsentUnavailable(
            "OS consent gate is only implemented on Windows; refusing to reveal without a live prompt"
        )

    verified = _try_windows_hello(clean_reason)
    if verified is not None:
        if not verified:
            raise ConsentDenied("Windows Hello verification was declined or failed")
        return True

    present = _try_windows_credential_prompt(clean_reason)
    if present is None:
        raise ConsentUnavailable("no OS consent mechanism is available on this machine")
    if not present:
        raise ConsentDenied("the consent prompt was cancelled")
    return True


def _try_windows_hello(reason: str) -> bool | None:
    """Real Windows Hello verification. Returns True/False, or None if unavailable.

    Uses the WinRT UserConsentVerifier, which shows the native Windows Security
    panel (PIN / fingerprint / face). Requires Windows Hello to be configured AND
    the `winsdk` (or legacy `winrt`) bridge to be importable; returns None otherwise
    so the caller can fall back.
    """
    try:
        try:
            from winsdk.windows.security.credentials.ui import (  # type: ignore
                UserConsentVerifier,
                UserConsentVerifierAvailability,
                UserConsentVerificationResult,
            )
        except Exception:
            from winrt.windows.security.credentials.ui import (  # type: ignore
                UserConsentVerifier,
                UserConsentVerifierAvailability,
                UserConsentVerificationResult,
            )
    except Exception:
        return None

    try:
        availability = UserConsentVerifier.check_availability_async().get()
        if availability != UserConsentVerifierAvailability.AVAILABLE:
            # Hello is not set up on this machine; let the caller fall back.
            return None
        result = UserConsentVerifier.request_verification_async(reason).get()
        return result == UserConsentVerificationResult.VERIFIED
    except Exception:
        return None


def _try_windows_credential_prompt(reason: str) -> bool | None:
    """Native interactive credential dialog as a PRESENCE gate.

    Returns True if the user submitted the dialog, False if they cancelled, or None
    if the API is unavailable. This does NOT verify the entered credentials - it only
    proves a human actively confirmed at the keyboard (weaker than Hello). The entered
    buffer is discarded immediately and never inspected.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    try:
        credui = ctypes.WinDLL("credui.dll")

        class CREDUI_INFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hwndParent", wintypes.HWND),
                ("pszMessageText", wintypes.LPCWSTR),
                ("pszCaptionText", wintypes.LPCWSTR),
                ("hbmBanner", wintypes.HBITMAP),
            ]

        info = CREDUI_INFOW()
        info.cbSize = ctypes.sizeof(CREDUI_INFOW)
        info.hwndParent = None
        info.pszMessageText = str(reason)
        info.pszCaptionText = "NULLA wallet - confirm it's you"
        info.hbmBanner = None

        auth_package = wintypes.DWORD(0)
        out_cred_blob = ctypes.c_void_p(None)
        out_cred_size = wintypes.DWORD(0)
        save = wintypes.BOOL(False)

        # CREDUIWIN_GENERIC (0x1): generic credentials, no logon validation - we only
        # care whether the user pressed OK (0) vs Cancel (ERROR_CANCELLED = 1223).
        CREDUIWIN_GENERIC = 0x00000001
        result = credui.CredUIPromptForWindowsCredentialsW(
            ctypes.byref(info),
            0,
            ctypes.byref(auth_package),
            None,
            0,
            ctypes.byref(out_cred_blob),
            ctypes.byref(out_cred_size),
            ctypes.byref(save),
            CREDUIWIN_GENERIC,
        )

        if out_cred_blob:
            # Wipe and free the returned credential buffer; we never read it.
            try:
                ctypes.memset(out_cred_blob, 0, out_cred_size.value)
            except Exception:
                pass
            ctypes.windll.ole32.CoTaskMemFree(out_cred_blob)

        if result == 0:
            return True
        if result == 1223:  # ERROR_CANCELLED
            return False
        # Any other return code: treat as unavailable so the caller fails closed.
        return None
    except Exception:
        return None
