"""
MongoDB database connection and configuration.

Indexes are declared in INDEXES and created once per process when the
connection is opened (API startup, or a Celery worker's first database
access), not on every collection access (issue #70).
"""
import logging
import os
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError

from app.exceptions import TransientError

load_dotenv()

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(TransientError):
    """MongoDB could not be reached (HTTP 503; Celery tasks retry it)."""


# These are read dynamically so test fixtures can override them
def get_mongodb_url():
    return os.getenv("MONGODB_URL", "mongodb://localhost:27017")

def get_database_name():
    return os.getenv("DATABASE_NAME", "elies_system")


IndexSpec = Tuple[Any, Dict[str, Any]]

# collection name -> [(keys, create_index options)]
INDEXES: Dict[str, List[IndexSpec]] = {
    "users": [
        ("username", {"unique": True}),
        ("email", {"unique": True}),
    ],
    "documents": [
        ("user_id", {}),
        ("uploaded_date", {}),
        ([("user_id", ASCENDING), ("uploaded_date", DESCENDING)], {}),
    ],
    "images": [
        ("user_id", {}),
        ("document_id", {}),
        ("uploaded_date", {}),
        ("source_type", {}),
        ([("user_id", ASCENDING), ("source_type", ASCENDING)], {}),
        ([("document_id", ASCENDING), ("source_type", ASCENDING)], {}),
    ],
    "single_annotations": [
        ("user_id", {}),
        ("image_id", {}),
        ("created_at", {}),
        ([("user_id", ASCENDING), ("image_id", ASCENDING)], {}),
        ([("image_id", ASCENDING), ("created_at", DESCENDING)], {}),
    ],
    "dual_annotations": [
        ("user_id", {}),
        ("source_image_id", {}),  # image where the annotation is drawn
        ("target_image_id", {}),  # linked target image
        ("link_id", {}),
        ("created_at", {}),
        ([("user_id", ASCENDING), ("source_image_id", ASCENDING)], {}),
        ([("user_id", ASCENDING), ("target_image_id", ASCENDING)], {}),
        ([("user_id", ASCENDING), ("link_id", ASCENDING)], {}),
        ([("source_image_id", ASCENDING), ("target_image_id", ASCENDING)], {}),
    ],
    "analyses": [
        ("user_id", {}),
        ("source_image_id", {}),
        ("target_image_id", {}),
        ("type", {}),
        ("status", {}),
        ("created_at", {}),
        # Analysis dashboard queries
        ([("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("user_id", ASCENDING), ("type", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("user_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("user_id", ASCENDING), ("source_image_id", ASCENDING)], {}),
    ],
    "image_relationships": [
        ("user_id", {}),
        ("image1_id", {}),
        ("image2_id", {}),
        ("source_type", {}),
        ("created_at", {}),
        # One relationship per image pair (IDs are stored sorted)
        ([("user_id", ASCENDING), ("image1_id", ASCENDING), ("image2_id", ASCENDING)], {"unique": True}),
        ([("user_id", ASCENDING), ("image2_id", ASCENDING)], {}),
    ],
    "indexing_jobs": [
        ("user_id", {}),
        ("status", {}),
        ("created_at", {}),
        ([("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
    ],
    "admin_audit_log": [
        ([("created_at", DESCENDING)], {}),
        ([("target_user_id", ASCENDING), ("created_at", DESCENDING)], {}),
    ],
    "jobs": [
        ("user_id", {}),
        ("job_type", {}),
        ("status", {}),
        ("created_at", {}),
        ([("user_id", ASCENDING), ("created_at", DESCENDING)], {}),
        ([("user_id", ASCENDING), ("job_type", ASCENDING), ("created_at", DESCENDING)], {}),
        # TTL: MongoDB deletes jobs once expires_at has passed
        ("expires_at", {"expireAfterSeconds": 0}),
    ],
}


def ensure_indexes(db) -> None:
    """
    Create every index in INDEXES (idempotent). A failing index, e.g. a
    unique index over existing duplicates, is logged and does not stop the
    others.
    """
    for collection_name, specs in INDEXES.items():
        collection = db[collection_name]
        for keys, options in specs:
            try:
                collection.create_index(keys, **options)
            except PyMongoError as e:
                logger.error("Could not create index %s on %s: %s", keys, collection_name, e)


class MongoDBConnection:
    """Singleton class for MongoDB connection"""
    _instance = None
    _client = None
    _db = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def connect(self) -> None:
        """
        Connect to MongoDB and make sure the indexes exist.

        Raises:
            DatabaseUnavailableError: MongoDB could not be reached
        """
        database_name = get_database_name()
        try:
            # tz_aware: datetimes come back as aware UTC, so the API emits them
            # with an explicit offset (clients otherwise read them as local time)
            client = MongoClient(get_mongodb_url(), serverSelectionTimeoutMS=5000, tz_aware=True)
            client.admin.command('ping')
        except PyMongoError as e:
            logger.error("MongoDB connection failed: %s", e)
            raise DatabaseUnavailableError("The database is unavailable. Please try again later.") from e
        self._client = client
        self._db = client[database_name]
        logger.info("Connected to MongoDB: %s", database_name)
        ensure_indexes(self._db)

    def disconnect(self) -> None:
        """Disconnect from MongoDB."""
        if self._client:
            self._client.close()
            logger.info("Disconnected from MongoDB")
        self.reset()

    def reset(self) -> None:
        """
        Forget the client without closing it. Used in forked Celery worker
        processes: a MongoClient must not be shared across a fork.
        """
        self._client = None
        self._db = None

    def get_database(self):
        """Get database instance (connecting on first use)"""
        if self._db is None:
            self.connect()
        return self._db

    def get_collection(self, collection_name: str):
        """Get a specific collection"""
        return self.get_database()[collection_name]


# Global database connection instance
db_connection = MongoDBConnection()


def get_users_collection():
    return db_connection.get_collection("users")


def get_documents_collection():
    """PDF documents uploaded by users"""
    return db_connection.get_collection("documents")


def get_images_collection():
    """Extracted and uploaded images"""
    return db_connection.get_collection("images")


def get_single_annotations_collection():
    """Single-image annotations"""
    return db_connection.get_collection("single_annotations")


def get_dual_annotations_collection():
    """Cross-image annotations"""
    return db_connection.get_collection("dual_annotations")


def get_analyses_collection():
    """Copy-move, TruFor, CBIR and provenance analyses"""
    return db_connection.get_collection("analyses")


def get_relationships_collection():
    """Image-to-image relationships"""
    return db_connection.get_collection("image_relationships")


def get_indexing_jobs_collection():
    """Batch CBIR indexing progress"""
    return db_connection.get_collection("indexing_jobs")


def get_jobs_collection():
    """Unified background job tracking (expires through a TTL index)"""
    return db_connection.get_collection("jobs")


def get_admin_audit_log_collection():
    """Audit trail of administrator actions"""
    return db_connection.get_collection("admin_audit_log")


def get_database():
    """Get database instance"""
    return db_connection.get_database()
