import hashlib
import hmac
import time
import pytest
from fastapi import HTTPException

from bormochecker.crypto import _b64u, sign
from bormochecker.web import _verify_access

SECRET = "test_passport_secret_key_12345"


@pytest.fixture(autouse=True)
def setup_secret(monkeypatch):
    monkeypatch.setenv("PASSPORT_HMAC_SECRET", SECRET)


def test_passport_signed_token_accepted():
    now = time.time()
    payload = {
        "uid": 1,
        "h": "ivan",
        "role": "teacher",
        "pro": int(now + 3600),
        "exp": int(now + 3600),
    }
    token = sign(SECRET.encode(), payload)
    res = _verify_access("education", bc_session=token)
    assert res["role"] == "teacher"
    assert res["allow_paid_search"] is False


def test_hex_signature_rejected(monkeypatch):
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "0")
    payload_b64 = _b64u(b'{"uid": 1, "role": "teacher", "pro": 9999999999}')
    hex_sig = hmac.new(SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    hex_token = f"{payload_b64}.{hex_sig}"
    res = _verify_access("education", bc_session=hex_token)
    assert res["role"] == "guest"


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "0")
    now = time.time()
    # Expired by exp
    payload = {
        "uid": 1,
        "role": "teacher",
        "pro": int(now + 3600),
        "exp": int(now - 10),
    }
    token = sign(SECRET.encode(), payload)
    res = _verify_access("education", bc_session=token)
    assert res["role"] == "guest"

    # Expired by pro_until for regular user
    payload2 = {
        "uid": 2,
        "role": "user",
        "pro_until": int(now - 10),
        "exp": int(now + 3600),
    }
    token2 = sign(SECRET.encode(), payload2)
    res2 = _verify_access("education", bc_session=token2)
    assert res2["role"] == "guest"


def test_tampered_payload_rejected(monkeypatch):
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "0")
    now = time.time()
    payload = {
        "uid": 1,
        "role": "teacher",
        "pro": int(now + 3600),
        "exp": int(now + 3600),
    }
    token = sign(SECRET.encode(), payload)
    body, sig = token.split(".")
    tampered_body = body[:-1] + ("A" if body[-1] != "A" else "B")
    tampered_token = f"{tampered_body}.{sig}"
    res = _verify_access("education", bc_session=tampered_token)
    assert res["role"] == "guest"


def test_garbage_cookie_gives_guest(monkeypatch):
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "0")
    res = _verify_access("education", bc_session="some.garbage.not.a.token")
    assert res["role"] == "guest"
    assert res["allow_paid_search"] is False

    # Also check with completely broken non-dotted garbage
    res2 = _verify_access("education", bc_session="!!!nonsense@@@")
    assert res2["role"] == "guest"

    # Also verify that when auth is required, garbage raises 401, not 500
    monkeypatch.setenv("FACTCHECK_REQUIRE_AUTH", "1")
    with pytest.raises(HTTPException) as excinfo:
        _verify_access("education", bc_session="broken_garbage")
    assert excinfo.value.status_code == 401
