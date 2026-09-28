"""Tests for the API skeleton: auth, the error envelope, strict schemas, the owner seed (Prompt 3).

"Done when": every route returns 401 in the envelope without `X-API-Key`, and an
invalid body returns 422 in the same envelope.
"""

import hashlib
import re
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.db.seed import OWNER_EMAIL, seed_owner
from app.models import User
from app.schemas import StrictModel
from main import create_app

KEY = "test-key-" + "x" * 32

# Architecture §10.2, typed out by hand so a change to the source can't pass silently.
CATALOGUE = {
    "VALIDATION_ERROR": 422,
    "AGENT_NOT_FOUND": 404,
    "WORKFLOW_NOT_FOUND": 404,
    "EXECUTION_NOT_FOUND": 404,
    "CAPABILITY_NOT_FOUND": 404,
    "WORKFLOW_CYCLE_DETECTED": 400,
    "UNAUTHORIZED": 401,
    "CAPABILITY_BUILD_FAILED": 500,
    "OUTPUT_DRIFT": 500,
}


class _Body(StrictModel):
    name: str
    count: int


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app():
    app = create_app()

    @app.post("/_test/body")
    def body(payload: _Body) -> dict:
        return payload.model_dump()

    @app.get("/_test/raise/{code}")
    def raise_(code: str) -> None:
        raise AppError(ErrorCode(code), "boom", {"id": "x"})

    @app.get("/_test/crash")
    def crash() -> None:
        raise RuntimeError("secret internals")

    return app


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def assert_envelope(resp, status: int, code: str) -> dict:
    assert resp.status_code == status
    body = resp.json()
    assert set(body) == {"error_code", "message", "details"}
    assert body["error_code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["details"], dict)
    return body


# --- FR-AUTH-001 -----------------------------------------------------------


def _every_route():
    for route in create_app().routes:
        if isinstance(route, APIRoute):
            path = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), route.path)
            for method in route.methods:
                yield method, path


@pytest.mark.parametrize("method,path", list(_every_route()))
def test_every_real_route_rejects_a_missing_key(client, method, path):
    assert_envelope(client.request(method, path), 401, "UNAUTHORIZED")


def test_wrong_key_is_rejected(client):
    assert_envelope(client.get("/health", headers={"X-API-Key": "wrong"}), 401, "UNAUTHORIZED")


def test_right_key_is_accepted(client):
    resp = client.get("/health", headers={"X-API-Key": KEY})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unset_api_key_fails_closed(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "")
    get_settings.cache_clear()
    assert_envelope(client.get("/health", headers={"X-API-Key": ""}), 401, "UNAUTHORIZED")


def test_auth_runs_before_body_validation(client):
    assert_envelope(client.post("/_test/body", json={"bogus": 1}), 401, "UNAUTHORIZED")


def test_routes_added_later_are_protected_too(client):
    assert_envelope(client.get("/_test/raise/AGENT_NOT_FOUND"), 401, "UNAUTHORIZED")


# --- FR-API-002 / FR-API-003 -----------------------------------------------


def test_valid_body_passes(client):
    resp = client.post("/_test/body", json={"name": "a", "count": 1}, headers={"X-API-Key": KEY})
    assert resp.status_code == 200


def test_unknown_field_is_rejected_with_422(client):
    resp = client.post("/_test/body", json={"name": "a", "count": 1, "extra": True}, headers={"X-API-Key": KEY})
    body = assert_envelope(resp, 422, "VALIDATION_ERROR")
    assert body["details"]["errors"][0]["loc"] == ["body", "extra"]


def test_missing_and_mistyped_fields_are_rejected_with_422(client):
    resp = client.post("/_test/body", json={"count": "many"}, headers={"X-API-Key": KEY})
    body = assert_envelope(resp, 422, "VALIDATION_ERROR")
    assert {tuple(e["loc"]) for e in body["details"]["errors"]} == {("body", "name"), ("body", "count")}


def test_malformed_json_is_rejected_with_422(client):
    resp = client.post("/_test/body", content=b"{not json", headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    assert_envelope(resp, 422, "VALIDATION_ERROR")


def test_validation_error_never_echoes_the_submitted_values(client):
    resp = client.post("/_test/body", json={"name": "a", "count": 1, "credentials": "hunter2"}, headers={"X-API-Key": KEY})
    assert resp.status_code == 422
    assert "hunter2" not in resp.text


# --- FR-API-004 ------------------------------------------------------------


def test_catalogue_is_exactly_architecture_10_2():
    assert {c.value for c in ErrorCode} == set(CATALOGUE)


@pytest.mark.parametrize("code,status", CATALOGUE.items())
def test_each_code_maps_to_its_status(client, code, status):
    body = assert_envelope(client.get(f"/_test/raise/{code}", headers={"X-API-Key": KEY}), status, code)
    assert body["details"] == {"id": "x"}


def test_unknown_route_uses_the_envelope(client):
    assert_envelope(client.get("/api/nope", headers={"X-API-Key": KEY}), 404, "NOT_FOUND")


def test_wrong_method_uses_the_envelope(client):
    assert_envelope(client.delete("/health", headers={"X-API-Key": KEY}), 405, "METHOD_NOT_ALLOWED")


def test_unhandled_exception_uses_the_envelope_and_hides_internals(client):
    resp = client.get("/_test/crash", headers={"X-API-Key": KEY})
    assert_envelope(resp, 500, "INTERNAL_ERROR")
    assert "secret internals" not in resp.text


# --- FR-AUTH-002 -----------------------------------------------------------


@pytest.fixture
def db():
    # Only the users table, which SQLite can hold; the full schema is Postgres-only.
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    with Session(engine) as session:
        yield session


def test_seed_creates_exactly_one_owner(db):
    seed_owner(db)
    seed_owner(db)
    assert db.scalar(select(func.count()).select_from(User)) == 1
    assert db.scalars(select(User)).one().email == OWNER_EMAIL


def test_seed_stores_a_hash_not_the_key(db):
    owner = seed_owner(db)
    assert owner.api_key_hash != KEY
    assert owner.api_key_hash == hashlib.sha256(KEY.encode()).hexdigest()


def test_seed_follows_a_rotated_key(db, monkeypatch):
    first = seed_owner(db).user_id
    monkeypatch.setenv("API_KEY", "rotated")
    get_settings.cache_clear()
    owner = seed_owner(db)
    assert owner.user_id == first
    assert owner.api_key_hash == hashlib.sha256(b"rotated").hexdigest()


def test_seed_refuses_without_a_key(db, monkeypatch):
    monkeypatch.setenv("API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        seed_owner(db)


# --- FR-AUTH-004 -----------------------------------------------------------


def test_secrets_never_appear_in_settings_repr(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-very-secret")
    monkeypatch.setenv("FERNET_KEY", "fernet-very-secret")
    get_settings.cache_clear()
    text = repr(get_settings())
    assert KEY not in text and "sk-very-secret" not in text and "fernet-very-secret" not in text
