from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_db
from backend.api.main import app


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def test_lists_all_registered_types(client):
    res = client.get("/node-types")
    assert res.status_code == 200
    body = res.json()
    types = {t["type"] for t in body}
    assert types >= {
        "finviz_screen", "manual_list", "cross", "threshold",
        "time_stop", "always", "never", "fixed_fraction",
    }


def test_empty_schema_types(client):
    body = {t["type"]: t for t in client.get("/node-types").json()}
    assert body["always"]["params_schema"] == []
    assert body["never"]["params_schema"] == []
    assert body["finviz_screen"]["params_schema"] == []


def test_cross_has_three_fields(client):
    body = {t["type"]: t for t in client.get("/node-types").json()}
    cross = body["cross"]
    field_names = {f["name"] for f in cross["params_schema"]}
    assert field_names == {"a", "b", "direction"}
    assert set(cross["allowed_kinds"]) == {"trigger", "exit"}
    direction_field = next(f for f in cross["params_schema"] if f["name"] == "direction")
    assert direction_field["type"] == "enum"
    assert set(direction_field["options"]) == {"up", "down"}
