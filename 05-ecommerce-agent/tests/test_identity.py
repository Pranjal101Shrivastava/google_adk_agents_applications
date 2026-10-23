"""Tests for verified-identity handling.

The vulnerability these guard against is specific: a conversational agent that
takes the customer's identity from the conversation. Every test here is a
variation of "the model must not be able to choose who it is acting as".
"""

from __future__ import annotations

import pytest

from ecommerce_agent.config import config
from ecommerce_agent.constants import StateKeys
from ecommerce_agent.identity import (
    IdentityError,
    apply_identity_to_state,
    clear_identity,
    get_verified_email,
    require_verified_email,
    verify_google_id_token,
)

CLAIMS = {
    "iss": "https://accounts.google.com",
    "aud": "test-client-id.apps.googleusercontent.com",
    "email": "alex.morgan@example.com",
    "email_verified": True,
    "name": "Alex Morgan",
}


# --- State handling ---------------------------------------------------
def test_verified_claims_are_written_to_state():
    state: dict = {}
    apply_identity_to_state(state, CLAIMS)
    assert state[StateKeys.VERIFIED_EMAIL] == "alex.morgan@example.com"
    assert state[StateKeys.VERIFIED_NAME] == "Alex Morgan"
    assert state[StateKeys.IS_AUTHENTICATED] is True


def test_name_falls_back_to_the_email_local_part():
    state: dict = {}
    apply_identity_to_state(state, {**CLAIMS, "name": None})
    assert state[StateKeys.VERIFIED_NAME] == "alex.morgan"


def test_clear_identity_returns_the_session_to_guest():
    state: dict = {}
    apply_identity_to_state(state, CLAIMS)
    clear_identity(state)
    assert state[StateKeys.IS_AUTHENTICATED] is False
    assert get_verified_email(state) is None


def test_guest_state_has_no_email(guest_state):
    assert get_verified_email(guest_state) is None


def test_signed_in_state_yields_the_email(signed_in_state):
    assert get_verified_email(signed_in_state) == "alex.morgan@example.com"


# --- The authorization boundary ---------------------------------------
def test_require_email_succeeds_when_signed_in(signed_in_state):
    assert require_verified_email(signed_in_state, "Viewing orders") == (
        "alex.morgan@example.com"
    )


def test_require_email_refuses_a_guest(guest_state):
    with pytest.raises(IdentityError, match="signed-in account"):
        require_verified_email(guest_state, "Viewing orders")


def test_refusal_tells_the_agent_not_to_accept_a_typed_address(guest_state):
    """This string is the countermeasure: it stops the "just ask them" workaround."""
    with pytest.raises(IdentityError) as exc:
        require_verified_email(guest_state, "Viewing orders")
    message = str(exc.value)
    assert "Do not ask them to type an email address" in message
    assert "a typed address is not an identity" in message


def test_an_email_in_state_without_authentication_is_ignored():
    """The flag is the gate; a stray email key must not be enough."""
    forged = {StateKeys.VERIFIED_EMAIL: "attacker@example.com"}
    assert get_verified_email(forged) is None
    with pytest.raises(IdentityError):
        require_verified_email(forged, "Viewing orders")


def test_authenticated_flag_without_an_email_is_ignored():
    half = {StateKeys.IS_AUTHENTICATED: True, StateKeys.VERIFIED_EMAIL: ""}
    assert get_verified_email(half) is None


# --- Token verification -----------------------------------------------
def test_verification_is_refused_without_a_client_id(monkeypatch):
    """Without an audience to check against, verification is just a decode."""
    monkeypatch.setattr(config, "google_client_id", None)
    with pytest.raises(IdentityError, match="GOOGLE_CLIENT_ID is not configured"):
        verify_google_id_token("some.jwt.token")


def test_empty_token_is_refused(monkeypatch):
    monkeypatch.setattr(config, "google_client_id", "test-client-id")
    with pytest.raises(IdentityError, match="No ID token"):
        verify_google_id_token("   ")


def test_a_malformed_token_is_refused(monkeypatch):
    monkeypatch.setattr(config, "google_client_id", "test-client-id")
    with pytest.raises(IdentityError, match="verification failed"):
        verify_google_id_token("not-a-jwt")


def test_a_wrong_issuer_is_refused(monkeypatch):
    """A validly signed token from elsewhere must not authenticate anyone."""
    monkeypatch.setattr(config, "google_client_id", "test-client-id")
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *a, **k: {**CLAIMS, "iss": "https://evil.test"},
    )
    with pytest.raises(IdentityError, match="Unexpected token issuer"):
        verify_google_id_token("token")


def test_an_unverified_email_is_refused(monkeypatch):
    """An unverified Google email can be an address the holder does not own."""
    monkeypatch.setattr(config, "google_client_id", "test-client-id")
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *a, **k: {**CLAIMS, "email_verified": False},
    )
    with pytest.raises(IdentityError, match="not verified with Google"):
        verify_google_id_token("token")


def test_a_token_without_an_email_claim_is_refused(monkeypatch):
    monkeypatch.setattr(config, "google_client_id", "test-client-id")
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *a, **k: {k2: v for k2, v in CLAIMS.items() if k2 != "email"},
    )
    with pytest.raises(IdentityError, match="no email claim"):
        verify_google_id_token("token")


def test_a_valid_token_returns_its_claims(monkeypatch):
    monkeypatch.setattr(config, "google_client_id", "test-client-id")
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token", lambda *a, **k: dict(CLAIMS)
    )
    claims = verify_google_id_token("Bearer-stripped.token.here")
    assert claims["email"] == "alex.morgan@example.com"


def test_the_audience_is_passed_to_the_verifier(monkeypatch):
    """Without the audience argument this would accept any Google token."""
    monkeypatch.setattr(config, "google_client_id", "my-client-id")
    captured: dict = {}

    def fake_verify(token, request, audience=None, *args, **kwargs):
        captured["audience"] = audience
        return dict(CLAIMS)

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", fake_verify)
    verify_google_id_token("token")
    assert captured["audience"] == "my-client-id"
