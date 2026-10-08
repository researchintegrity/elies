"""Indexes created once (#70), storage accounting (#71) and relationship graphs (#72)."""
import pathlib
from pathlib import Path

import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError

import app.routes.cbir as cbir_routes
from app.celery_config import reset_database_connection
from app.config.settings import UPLOAD_DIR
from app.db import mongodb
from app.db.mongodb import (
    get_analyses_collection,
    get_documents_collection,
    get_images_collection,
    get_relationships_collection,
    get_users_collection,
)
from app.exceptions import TransientError
from app.main import app
from app.services import relationship_service
from app.services.relationship_service import (
    compute_max_spanning_tree,
    create_relationship,
    get_relationship_graph,
    get_relationships_for_image,
)
from app.services.storage_service import (
    get_storage_used,
    reconcile_all_storage,
    reserve_storage,
)
from app.tasks.lifecycle import TrackedJob, run_analysis
from app.utils.file_storage import analysis_output_dir
from tests.unit.conftest import PDF_BYTES, PNG_BYTES


# ---------------------------------------------------------------- #70 ----

def test_collection_getters_do_not_create_indexes(mock_db, monkeypatch):
    calls = []
    monkeypatch.setattr(mongomock.collection.Collection, "create_index",
                        lambda self, *a, **k: calls.append(self.name))
    for getter in (mongodb.get_users_collection, mongodb.get_documents_collection, mongodb.get_images_collection,
                   mongodb.get_single_annotations_collection, mongodb.get_dual_annotations_collection,
                   mongodb.get_analyses_collection, mongodb.get_relationships_collection,
                   mongodb.get_indexing_jobs_collection, mongodb.get_jobs_collection):
        getter()
    assert calls == []


def test_ensure_indexes_creates_unique_and_ttl_indexes(mock_db):
    users = get_users_collection()
    users.insert_one({"username": "carol", "email": "c@example.org"})
    with pytest.raises(DuplicateKeyError):
        users.insert_one({"username": "carol", "email": "other@example.org"})
    ttl = [i for i in mock_db["jobs"].index_information().values() if "expireAfterSeconds" in i]
    assert ttl and ttl[0]["key"] == [("expires_at", 1)]


class _UnreachableClient:
    def __init__(self, *args, **kwargs):
        self.admin = self

    def command(self, *args, **kwargs):
        raise ServerSelectionTimeoutError("no servers")


def test_unreachable_database_is_a_transient_error(monkeypatch):
    monkeypatch.setattr(mongodb, "MongoClient", _UnreachableClient)
    connection = mongodb.MongoDBConnection()
    monkeypatch.setattr(connection, "_db", None)
    with pytest.raises(mongodb.DatabaseUnavailableError) as raised:
        connection.connect()
    assert isinstance(raised.value, TransientError)
    assert connection._db is None


def test_requests_get_503_while_the_database_is_down(client, alice, monkeypatch):
    def down():
        raise mongodb.DatabaseUnavailableError("The database is unavailable. Please try again later.")

    monkeypatch.setattr(mongodb.db_connection, "_db", None)
    monkeypatch.setattr(mongodb.db_connection, "connect", down)
    response = client.get("/users/me", headers=alice.headers)
    assert response.status_code == 503
    assert "database is unavailable" in response.json()["detail"]


def test_api_startup_fails_fast_without_database(monkeypatch):
    def down():
        raise mongodb.DatabaseUnavailableError()

    monkeypatch.setattr(mongodb.db_connection, "connect", down)
    with pytest.raises(mongodb.DatabaseUnavailableError):
        with TestClient(app):
            pass


def test_forked_worker_drops_the_parent_client(mock_db, monkeypatch):
    reset_database_connection()
    assert mongodb.db_connection._client is None and mongodb.db_connection._db is None


# ---------------------------------------------------------------- #71 ----

def _used(user):
    return get_storage_used(user.id)


def _upload(client, user, content=PNG_BYTES, name="a.png"):
    return client.post("/images/upload", headers=user.headers, files={"file": (name, content, "image/png")})


def test_upload_and_delete_keep_the_counter_exact(client, alice):
    response = _upload(client, alice)
    assert response.status_code == 201, response.text
    body = response.json()
    assert _used(alice) == len(PNG_BYTES) == body["user_storage_used"]

    # The generated thumbnail counts too, and is released with the image
    assert client.get(f"/images/{body['_id']}/thumbnail", headers=alice.headers).status_code == 200
    assert _used(alice) > len(PNG_BYTES)

    assert client.delete(f"/images/{body['_id']}", headers=alice.headers).status_code == 204
    assert _used(alice) == 0


def test_rejected_upload_releases_its_reservation(client, alice):
    response = _upload(client, alice, content=b"not an image" * 10)
    assert response.status_code == 400
    assert _used(alice) == 0


def test_quota_is_enforced_from_the_counter(client, alice):
    get_users_collection().update_one({"_id": ObjectId(alice.id)},
                                      {"$set": {"storage_limit_bytes": len(PNG_BYTES) + 10}})
    assert _upload(client, alice).status_code == 201
    response = _upload(client, alice)
    assert response.status_code == 413
    assert _used(alice) == len(PNG_BYTES)
    assert len(list((UPLOAD_DIR / alice.id / "images" / "uploaded").iterdir())) == 1


def test_reservations_cannot_overshoot_the_quota(mock_db):
    user_id = get_users_collection().insert_one(
        {"username": "dave", "email": "d@example.org", "storage_used_bytes": 0, "storage_limit_bytes": 150}
    ).inserted_id
    user = {"_id": user_id, "storage_limit_bytes": 150}
    reserve_storage(user, 100)
    with pytest.raises(Exception) as raised:
        reserve_storage(user, 100)  # would be 200 > 150
    assert raised.value.status_code == 413
    reserve_storage(user, 50)
    assert get_storage_used(str(user_id)) == 150


def test_listing_never_walks_the_workspace(client, alice, monkeypatch):
    assert client.post("/documents/upload", headers=alice.headers,
                       files={"file": ("a.pdf", PDF_BYTES, "application/pdf")}).status_code == 201
    assert _upload(client, alice).status_code == 201

    def no_walk(self, pattern):
        raise AssertionError("request walked the workspace")

    monkeypatch.setattr(pathlib.Path, "rglob", no_walk)
    for path in ("/documents", "/images"):
        response = client.get(path, headers=alice.headers)
        assert response.status_code == 200, response.text
    items = client.get("/documents", headers=alice.headers).json()["items"]
    assert items[0]["user_storage_used"] == len(PDF_BYTES) + len(PNG_BYTES)


def test_reconcile_corrects_drift(client, alice):
    assert _upload(client, alice).status_code == 201
    get_users_collection().update_one({"_id": ObjectId(alice.id)}, {"$set": {"storage_used_bytes": 999_999}})
    corrected = reconcile_all_storage()
    assert corrected[alice.id] == len(PNG_BYTES) - 999_999
    assert _used(alice) == len(PNG_BYTES)


def test_analysis_outputs_count_against_the_quota(mock_db):
    user_id = str(get_users_collection().insert_one({"username": "erin", "email": "e@example.org"}).inserted_id)
    analysis_id = str(get_analyses_collection().insert_one({"user_id": user_id, "status": "pending"}).inserted_id)
    output_dir = analysis_output_dir(user_id, analysis_id, "trufor")

    def work():
        output_dir.mkdir(parents=True)
        (output_dir / "map.png").write_bytes(b"x" * 1234)
        return True, "ok", {}

    run_analysis(None, TrackedJob(user_id, None, analysis_id), "start", work, output_dir=output_dir)
    assert get_storage_used(user_id) == 1234


def test_screening_result_is_validated_and_stored_under_a_fixed_name(client, alice):
    image_id = _upload(client, alice).json()["_id"]
    data = {"image_id": image_id, "analysis_subtype": "../../../escape"}

    bad = client.post("/analyses/screening-tool", headers=alice.headers, data=data,
                      files={"result_image": ("r.png", b"not an image", "image/png")})
    assert bad.status_code == 400
    assert get_analyses_collection().count_documents({"type": "screening_tool"}) == 0

    ok = client.post("/analyses/screening-tool", headers=alice.headers, data=data,
                     files={"result_image": ("r.png", PNG_BYTES, "image/png")})
    assert ok.status_code == 201, ok.text
    stored = Path(ok.json()["results"]["result_image"])
    assert stored.name == "result.png"
    assert stored.parent == analysis_output_dir(alice.id, ok.json()["_id"], "screening_tool")
    assert _used(alice) == 2 * len(PNG_BYTES)


def test_cbir_query_upload_is_size_limited(client, alice, monkeypatch):
    monkeypatch.setattr(cbir_routes, "MAX_IMAGE_FILE_SIZE", 10)
    response = client.post("/cbir/search/upload", headers=alice.headers,
                           files={"file": ("q.png", PNG_BYTES, "image/png")})
    assert response.status_code == 400
    assert "too large" in response.json()["detail"]


def test_document_deletion_releases_pdf_and_extracted_images(client, alice):
    doc_id = client.post("/documents/upload", headers=alice.headers,
                         files={"file": ("a.pdf", PDF_BYTES, "application/pdf")}).json()["_id"]
    extracted_dir = UPLOAD_DIR / alice.id / "images" / "extracted" / doc_id
    extracted_dir.mkdir(parents=True, exist_ok=True)
    (extracted_dir / "img.png").write_bytes(PNG_BYTES)
    get_users_collection().update_one({"_id": ObjectId(alice.id)}, {"$inc": {"storage_used_bytes": len(PNG_BYTES)}})
    get_images_collection().insert_one({"user_id": alice.id, "document_id": doc_id, "source_type": "extracted",
                                        "file_path": str(extracted_dir / "img.png")})

    assert client.delete(f"/documents/{doc_id}", headers=alice.headers).status_code == 204
    assert _used(alice) == 0
    assert get_documents_collection().count_documents({}) == 0


# ---------------------------------------------------------------- #72 ----

class _CountingCollection:
    def __init__(self, collection):
        self._collection = collection
        self.finds = 0

    def find(self, *args, **kwargs):
        self.finds += 1
        return self._collection.find(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._collection, name)


def _images(user_id, count):
    ids = [str(i) for i in get_images_collection().insert_many(
        [{"user_id": user_id, "filename": f"{n}.png", "original_filename": f"fig{n}.png"} for n in range(count)]
    ).inserted_ids]
    return ids


def _link(user_id, a, b, weight=1.0):
    create_relationship(user_id, a, b, "manual", weight=weight)


def test_graph_traversal_is_batched_by_level(mock_db, monkeypatch):
    ids = _images("u1", 40)
    for a, b in zip(ids, ids[1:3]):  # a short chain, then a big star around ids[2]
        _link("u1", a, b)
    for leaf in ids[3:]:
        _link("u1", ids[2], leaf)
    counting = _CountingCollection(get_relationships_collection())
    monkeypatch.setattr(relationship_service, "get_relationships_collection", lambda: counting)

    graph = get_relationship_graph(ids[0], "u1", max_depth=0)

    assert graph["total_nodes_count"] == 40 and len(graph["nodes"]) == 40
    assert counting.finds <= 4  # one query per BFS level, not per node
    assert graph["nodes"][0]["is_query"] and graph["nodes"][0]["label"] == "fig0.png"


def test_graph_depth_limits_nodes_and_edges_but_counts_the_component(mock_db):
    a, b, c, d = _images("u1", 4)
    _link("u1", a, b)
    _link("u1", b, c)
    _link("u1", c, d)

    graph = get_relationship_graph(a, "u1", max_depth=1)

    assert {n["id"] for n in graph["nodes"]} == {a, b}
    assert {(e["source"], e["target"]) for e in graph["edges"]} == {tuple(sorted((a, b)))}
    assert graph["total_nodes_count"] == 4 and graph["truncated"] is False


def test_graph_exploration_is_capped(mock_db):
    ids = _images("u1", 12)
    for leaf in ids[1:]:
        _link("u1", ids[0], leaf)

    graph = get_relationship_graph(ids[0], "u1", max_depth=0, max_nodes=5)

    assert graph["total_nodes_count"] == 5 and graph["truncated"] is True
    assert len(graph["nodes"]) == 5
    # No edge may point at an image left out by the cap, and the MST spans every node
    node_ids = {n["id"] for n in graph["nodes"]}
    assert len(graph["edges"]) == 4
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in graph["edges"])
    assert len(graph["mst_edges"]) == 4


def test_graph_endpoint_reports_truncation(client, alice):
    first = _upload(client, alice).json()["_id"]
    response = client.get(f"/relationships/image/{first}/graph", headers=alice.headers)
    assert response.status_code == 200, response.text
    assert response.json()["truncated"] is False and response.json()["total_nodes_count"] == 1


def test_max_spanning_tree_prefers_heavy_edges():
    edges = [
        {"source": "A", "target": "B", "weight": 0.9},
        {"source": "B", "target": "C", "weight": 0.8},
        {"source": "A", "target": "C", "weight": 0.1},
        {"source": "C", "target": "D", "weight": 0.5},
        {"source": "B", "target": "D", "weight": 0.2},
    ]
    mst = compute_max_spanning_tree(["A", "B", "C", "D"], edges)
    assert {(e["source"], e["target"]) for e in mst} == {("A", "B"), ("B", "C"), ("C", "D")}
    assert all(e["is_mst_edge"] for e in mst)


def test_create_relationship_is_an_idempotent_upsert(mock_db):
    a, b = _images("u1", 2)
    first = create_relationship("u1", a, b, "provenance", weight=0.4, metadata={"run": 1})
    again = create_relationship("u1", b, a, "provenance", weight=0.2, metadata={"run": 2})
    stronger = create_relationship("u1", a, b, "provenance", weight=0.7, metadata={"run": 3})

    assert first["_id"] == again["_id"] == stronger["_id"]
    stored = list(get_relationships_collection().find({"user_id": "u1"}))
    assert len(stored) == 1
    assert stored[0]["weight"] == 0.7 and stored[0]["metadata"] == {"run": 3}
    assert get_images_collection().count_documents({"is_flagged": True}) == 2


def test_relationships_for_image_are_enriched_in_one_query(mock_db, monkeypatch):
    a, b, c = _images("u1", 3)
    _link("u1", a, b)
    _link("u1", a, c)
    get_images_collection().delete_one({"_id": ObjectId(c)})
    counting = _CountingCollection(get_images_collection())
    monkeypatch.setattr(relationship_service, "get_images_collection", lambda: counting)

    rels = get_relationships_for_image(a, "u1")

    assert counting.finds == 1
    others = {r["image1_id"] if r["image2_id"] == a else r["image2_id"]: r["other_image"] for r in rels}
    assert others[b]["filename"] == "fig1.png"
    assert others[c] is None


def test_cli_reconciles_storage(client, alice, capsys):
    from app.cli import main

    get_users_collection().update_one({"_id": ObjectId(alice.id)}, {"$set": {"storage_used_bytes": 42}})
    assert main(["reconcile-storage"]) == 0
    assert "corrected for 1 users" in capsys.readouterr().out
    assert _used(alice) == 0
