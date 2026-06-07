import time
from unittest import mock
from src.helpers import generate_parental_access_code

def test_generate_parental_access_code_deterministic():
    secret = "test_agent_secret_key_12345"
    
    # Verify code is consistent at the same time step
    with mock.patch("time.time", return_value=1717780000):  # time slot 1
        code1 = generate_parental_access_code(secret)
        code2 = generate_parental_access_code(secret)
        assert code1 == code2
        assert len(code1) == 6
        assert code1.isdigit()

    # Verify code changes when time moves past the 30-minute window
    with mock.patch("time.time", return_value=1717780000 + 1800):  # time slot 2
        code3 = generate_parental_access_code(secret)
        assert code1 != code3
        assert len(code3) == 6

def test_generate_parental_access_code_empty_secret():
    # An empty or missing token should fail gracefully or return default/fallback
    assert generate_parental_access_code("") == "000000"
    assert generate_parental_access_code(None) == "000000"
