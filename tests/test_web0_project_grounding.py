from __future__ import annotations

from core.web0_project_grounding import (
    NULL_REGISTRAR_V2_PROGRAM,
    web0_null_project_response,
)


def test_general_null_buy_question_is_grounded() -> None:
    result = web0_null_project_response("can we buy a .null address?")
    assert result is not None
    text = result["response"]
    assert "null_registrar v2" in text
    assert "nulla resolve <name>.null" in text
    # Fee model must be stated correctly: free in pilot, rent-only, no recipient.
    assert "no fee" in text.lower() or "no address to send" in text.lower()


def test_named_registration_request_refuses_auto_spend_and_points_to_resolve() -> None:
    result = web0_null_project_response(
        "can you help me to buy a .null name? I want for example test123.null"
    )
    assert result is not None
    text = result["response"]
    assert "`test123.null`" in text
    assert "nulla resolve test123.null" in text
    assert "will not sign, spend, or submit" in text


def test_fee_followup_without_null_token_is_answered_not_dead_ended() -> None:
    # This is the exact turn that used to dead-end with "I couldn't map that cleanly to a
    # real action": a registration follow-up whose .null token was in a previous message.
    result = web0_null_project_response(
        "ok lets grab it, tell me where to send the registration fee and you will do it for me"
    )
    assert result is not None
    assert result["intent"] == "web0_null_registration_fee_followup"
    text = result["response"].lower()
    # Correct the user's mental model: there is no fee to send, and no recipient.
    assert "no fee" in text or "no registration fee" in text
    assert "rent" in text
    # Stay honest about capability: NULLA can't auto-sign a registration yet.
    assert "can't sign" in text or "cannot sign" in text


def test_registration_word_alone_does_not_over_fire() -> None:
    # A plain "register" with no fee/exec phrasing must not trigger a .null answer.
    assert web0_null_project_response("how do I register for the newsletter") is None
    assert web0_null_project_response("hi, what's up?") is None


def test_program_id_is_surfaced_for_named_requests() -> None:
    result = web0_null_project_response("register mycoolname.null for me please")
    assert result is not None
    assert NULL_REGISTRAR_V2_PROGRAM in result["response"]
