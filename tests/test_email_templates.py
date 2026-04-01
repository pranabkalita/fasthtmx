from app.services.email_service import render_email_bodies


def _context() -> dict[str, object]:
    return {
        "subject": "Test Subject",
        "preheader": "Test preheader",
        "action_url": "https://example.com/action",
        "expires_hours": 24,
        "expires_minutes": 30,
        "user_name": "Pranab",
    }


def test_render_verify_account_email_templates() -> None:
    html, text = render_email_bodies("verify_account", _context())

    assert "FastAuth" in html
    assert "Verify Your Email" in html
    assert "https://example.com/action" in html
    assert "https://example.com/action" in text


def test_render_all_transactional_templates() -> None:
    names = [
        "verify_account",
        "verify_account_resend",
        "reset_password",
        "verify_new_email",
    ]

    for template_name in names:
        html, text = render_email_bodies(template_name, _context())
        assert "FastAuth" in html
        assert "https://example.com/action" in html
        assert "https://example.com/action" in text
