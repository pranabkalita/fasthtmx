from app.services.auth_service import generate_backup_code_values


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
