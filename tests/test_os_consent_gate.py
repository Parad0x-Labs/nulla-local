from __future__ import annotations

import pytest

from core import os_consent_gate
from core.os_consent_gate import (
    ConsentDenied,
    ConsentUnavailable,
    require_os_user_consent,
    set_consent_override_for_tests,
)


@pytest.fixture(autouse=True)
def _clear_override_and_env(monkeypatch):
    monkeypatch.delenv("NULLA_WALLET_SKIP_CONSENT_GATE", raising=False)
    set_consent_override_for_tests(None)
    yield
    set_consent_override_for_tests(None)


def test_override_granted_allows_consent():
    set_consent_override_for_tests(lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_override_denied_returns_false():
    set_consent_override_for_tests(lambda reason: False)
    assert require_os_user_consent("reveal key") is False


def test_env_bypass_requires_literal_yes(monkeypatch):
    monkeypatch.setenv("NULLA_WALLET_SKIP_CONSENT_GATE", "yes")
    assert require_os_user_consent("reveal key") is True


def test_env_bypass_ignores_non_yes_values(monkeypatch):
    # Anything other than the literal "yes" must NOT bypass the gate.
    monkeypatch.setenv("NULLA_WALLET_SKIP_CONSENT_GATE", "1")
    monkeypatch.setattr(os_consent_gate.sys, "platform", "linux")
    with pytest.raises(ConsentUnavailable):
        require_os_user_consent("reveal key")


def test_non_windows_fails_closed(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "linux")
    with pytest.raises(ConsentUnavailable):
        require_os_user_consent("reveal key")


def test_windows_hello_verified_grants(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_windows_hello_declined_raises_denied(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: False)
    with pytest.raises(ConsentDenied):
        require_os_user_consent("reveal key")


def test_falls_back_to_credential_prompt_when_hello_unavailable(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_windows_credential_prompt", lambda reason: True)
    assert require_os_user_consent("reveal key") is True


def test_credential_prompt_cancelled_raises_denied(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_windows_credential_prompt", lambda reason: False)
    with pytest.raises(ConsentDenied):
        require_os_user_consent("reveal key")


def test_no_mechanism_fails_closed(monkeypatch):
    monkeypatch.setattr(os_consent_gate.sys, "platform", "win32")
    monkeypatch.setattr(os_consent_gate, "_try_windows_hello", lambda reason: None)
    monkeypatch.setattr(os_consent_gate, "_try_windows_credential_prompt", lambda reason: None)
    with pytest.raises(ConsentUnavailable):
        require_os_user_consent("reveal key")
