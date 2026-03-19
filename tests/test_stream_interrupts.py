from codex_a2a_server.stream_interrupts import extract_interrupt_asked_event


def test_extract_permission_interrupt_keeps_display_message_and_aliases() -> None:
    event = {
        "type": "permission.asked",
        "properties": {
            "id": "perm-1",
            "permission": "approval",
            "patterns": ["/repo/.env"],
            "always": ["/repo/.env.example"],
            "display_message": "Agent wants to read the environment file.",
            "displayMessage": "Legacy display alias.",
            "description": "Fallback description.",
            "reason": "The command needs confirmation.",
        },
    }

    assert extract_interrupt_asked_event(event) == {
        "request_id": "perm-1",
        "interrupt_type": "permission",
        "details": {
            "permission": "approval",
            "patterns": ["/repo/.env"],
            "always": ["/repo/.env.example"],
            "display_message": "Agent wants to read the environment file.",
            "displayMessage": "Legacy display alias.",
            "description": "Fallback description.",
            "reason": "The command needs confirmation.",
        },
        "codex_private": {},
    }


def test_extract_question_interrupt_keeps_display_message_and_aliases() -> None:
    event = {
        "type": "question.asked",
        "properties": {
            "id": "q-1",
            "questions": [{"id": "q1", "question": "Proceed with deployment?"}],
            "display_message": "Please confirm how the agent should continue.",
            "prompt": "Proceed with deployment?",
            "description": "Deployment will update the production service.",
        },
    }

    assert extract_interrupt_asked_event(event) == {
        "request_id": "q-1",
        "interrupt_type": "question",
        "details": {
            "questions": [{"id": "q1", "question": "Proceed with deployment?"}],
            "display_message": "Please confirm how the agent should continue.",
            "prompt": "Proceed with deployment?",
            "description": "Deployment will update the production service.",
        },
        "codex_private": {},
    }


def test_extract_permission_interrupt_keeps_nested_request_details() -> None:
    event = {
        "type": "permission.asked",
        "properties": {
            "id": "perm-2",
            "permission": "approval",
            "request": {
                "description": "Agent wants to read the environment file.",
                "reason": "The command needs confirmation.",
            },
        },
    }

    assert extract_interrupt_asked_event(event) == {
        "request_id": "perm-2",
        "interrupt_type": "permission",
        "details": {
            "permission": "approval",
            "patterns": [],
            "always": [],
            "request": {
                "description": "Agent wants to read the environment file.",
                "reason": "The command needs confirmation.",
            },
            "display_message": "Agent wants to read the environment file.",
        },
        "codex_private": {},
    }


def test_extract_question_interrupt_keeps_nested_context_and_question_fallback() -> None:
    event = {
        "type": "question.asked",
        "properties": {
            "id": "q-2",
            "context": {
                "description": "Please confirm how the agent should continue.",
                "questions": [{"id": "q1", "question": "Proceed with deployment?"}],
            },
        },
    }

    assert extract_interrupt_asked_event(event) == {
        "request_id": "q-2",
        "interrupt_type": "question",
        "details": {
            "questions": [{"id": "q1", "question": "Proceed with deployment?"}],
            "context": {
                "description": "Please confirm how the agent should continue.",
                "questions": [{"id": "q1", "question": "Proceed with deployment?"}],
            },
            "display_message": "Please confirm how the agent should continue.",
        },
        "codex_private": {},
    }
