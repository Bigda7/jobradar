from jobradar.security import redact_sensitive_text, redact_sensitive_value


def test_sensitive_text_redacts_urls_headers_assignments_and_telegram_tokens() -> None:
    value = (
        "postgresql://user:real-password@db:5432/app?api_key=real-api-key "
        "Authorization: Bearer real-bearer-token "
        "TELEGRAM_BOT_TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
    )

    redacted = redact_sensitive_text(value)

    assert "real-password" not in redacted
    assert "real-api-key" not in redacted
    assert "real-bearer-token" not in redacted
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef" not in redacted
    assert redacted.count("[REDACTED]") >= 4


def test_sensitive_values_redact_nested_secret_keys() -> None:
    redacted = redact_sensitive_value(
        "payload",
        {"api_key": "secret", "nested": {"password": "password"}, "status": "ok"},
    )

    assert redacted == {
        "api_key": "[REDACTED]",
        "nested": {"password": "[REDACTED]"},
        "status": "ok",
    }
