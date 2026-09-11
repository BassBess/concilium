"""Credential encryption, password hashing, signed tokens, input validation."""
from __future__ import annotations

import pytest

from app.security import (
    decrypt,
    encrypt,
    hash_password,
    is_encrypted,
    mask_secret,
    sign_token,
    verify_password,
    verify_token,
)


def test_encryption_roundtrip():
    secret = "sk-test-1234567890abcdef"
    enc = encrypt(secret)
    assert is_encrypted(enc)
    assert secret not in enc
    assert decrypt(enc) == secret
    # tampering is detected
    tampered = enc[:-4] + ("aaaa" if not enc.endswith("aaaa") else "bbbb")
    with pytest.raises(Exception):
        decrypt(tampered)


def test_password_hashing():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_signed_tokens():
    t = sign_token({"sub": "local"})
    assert verify_token(t)["sub"] == "local"
    assert verify_token(t + "x") is None
    assert verify_token("garbage.payload") is None
    expired = sign_token({"sub": "x"}, ttl_seconds=-10)
    assert verify_token(expired) is None


def test_masking():
    assert mask_secret("") == ""
    m = mask_secret("sk-1234567890abcdef")
    assert "1234567890" not in m and "•" in m


async def test_run_input_validation(client):
    # missing project → 404
    r = await client.post("/api/conversations", json={"project_id": "missing", "mode": "council"})
    assert r.status_code == 404

    # create a real conversation to test council validation
    pid = (await client.post("/api/projects", json={"name": "val2"})).json()["id"]
    cid = (await client.post("/api/conversations",
                             json={"project_id": pid, "mode": "council"})).json()["id"]
    r = await client.post(f"/api/conversations/{cid}/runs",
                          json={"question": "x", "mode": "council", "config": {"participants": []}})
    assert r.status_code == 400
    r = await client.post(f"/api/conversations/{cid}/runs",
                          json={"question": "", "mode": "single", "config": {}})
    assert r.status_code == 400
    r = await client.post(f"/api/conversations/{cid}/runs",
                          json={"question": "x", "mode": "bogus", "config": {}})
    assert r.status_code == 400


async def test_auth_status_open_by_default(client):
    r = await client.get("/api/auth/status")
    assert r.json()["password_required"] is False
