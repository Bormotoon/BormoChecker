import io
import logging
from unittest.mock import patch

from bormochecker.log_masking import (
    install_secret_masker,
    mask_sensitive_data,
)


def test_mask_sensitive_data_patterns():
    # Bearer and Api-Key
    assert mask_sensitive_data("Authorization: Bearer mysecrettoken12345") == "Authorization: Bearer ***"
    assert mask_sensitive_data("Authorization: Api-Key key_abcdef987654") == "Authorization: Api-Key ***"

    # URL basic auth
    assert (
        mask_sensitive_data("https://user:mypassword123@example.com/api")
        == "https://user:***@example.com/api"
    )

    # Key-value / JSON pairs
    assert mask_sensitive_data('api_key="supersecret"') == 'api_key="***"'
    assert mask_sensitive_data('{"password": "secret_pass_123"}') == '{"password": "***"}'
    assert mask_sensitive_data("access_token=xyz987654321") == "access_token=***"

    # Query params
    assert (
        mask_sensitive_data("https://api.example.com/search?api_key=secretkey123&query=test")
        == "https://api.example.com/search?api_key=***&query=test"
    )


def test_mask_env_secrets():
    with patch.dict("os.environ", {"FACTCHECK_INTERNAL_TOKEN": "very_secret_internal_token_value"}):
        text = "Request rejected for token: very_secret_internal_token_value in validation"
        assert mask_sensitive_data(text) == "Request rejected for token: *** in validation"


def test_secret_masking_filter_on_logger():
    test_logger = logging.getLogger("test_masking_logger")
    test_logger.setLevel(logging.INFO)
    test_logger.handlers.clear()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    test_logger.addHandler(handler)

    install_secret_masker(test_logger)

    test_logger.info("User logged in with password=%s", "super_secret_password_123")
    test_logger.warning("Header Authorization: Bearer token_xyz_123456")

    output = stream.getvalue()
    assert "super_secret_password_123" not in output
    assert "token_xyz_123456" not in output
    assert "password=***" in output
    assert "Bearer ***" in output
