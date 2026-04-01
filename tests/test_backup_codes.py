from app.services.auth_service import generate_backup_code_values
from app.db.models import User
from app.services.auth_service import get_user_totp_secret, set_user_totp_secret


def test_generate_backup_codes_count_and_uniqueness() -> None:
    codes = generate_backup_code_values(count=8)
    assert len(codes) == 8
    assert len(set(codes)) == 8


def test_generate_backup_codes_format() -> None:
    codes = generate_backup_code_values(count=4)
    for code in codes:
        left, right = code.split("-")
        assert len(left) == 4
        assert len(right) == 4
        assert left.isalnum()
        assert right.isalnum()


def test_totp_secret_is_encrypted_on_write() -> None:
    user = User(email="enc@example.com", password_hash="hash")
    set_user_totp_secret(user, "TOPSECRET")
    assert user.two_factor_secret is None
    assert user.two_factor_secret_encrypted is not None
    assert user.two_factor_secret_encrypted != "TOPSECRET"
    assert get_user_totp_secret(user) == "TOPSECRET"


def test_totp_secret_falls_back_to_legacy_column() -> None:
    user = User(email="legacy@example.com", password_hash="hash")
    user.two_factor_secret = "LEGACYSECRET"
    assert get_user_totp_secret(user) == "LEGACYSECRET"
