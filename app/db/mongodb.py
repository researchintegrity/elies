"""
MongoDB database connection and configuration
"""
import logging
import os

from dotenv import load_dotenv
from fastapi import HTTPException, status
from pymongo import MongoClient

load_dotenv()

logger = logging.getLogger(__name__)

# These are read dynamically so test fixtures can override them
def get_mongodb_url():
    return os.getenv("MONGODB_URL", "mongodb://localhost:27017")

def get_database_name():
    return os.getenv("DATABASE_NAME", "elies_system")


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
        """Connect to MongoDB."""
        try:
            mongodb_url = get_mongodb_url()
            database_name = get_database_name()
            self._client = MongoClient(mongodb_url, serverSelectionTimeoutMS=5000)
            self._client.admin.command('ping')
            self._db = self._client[database_name]
            logger.info("Connected to MongoDB: %s", database_name)
        except Exception as e:
            logger.error("MongoDB connection failed: %s", str(e))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"MongoDB connection failed: {str(e)}"
            )

    def disconnect(self) -> None:
        """Disconnect from MongoDB."""
        if self._client:
            self._client.close()
            logger.info("Disconnected from MongoDB")

    def get_database(self):
        """Get database instance"""
        if self._db is None:
            self.connect()
        return self._db

    def get_collection(self, collection_name: str):
        """Get a specific collection"""
        db = self.get_database()
        return db[collection_name]


# Global database connection instance
db_connection = MongoDBConnection()


def get_users_collection():
    """Get users collection with indexes"""
    collection = db_connection.get_collection("users")
    
    # Create indexes for better performance
    collection.create_index("username", unique=True)
    collection.create_index("email", unique=True)
    
    return collection


def get_documents_collection():
    """Get documents collection with indexes for PDF uploads"""
    collection = db_connection.get_collection("documents")
    
    # Create indexes for better performance
    collection.create_index("user_id")
    collection.create_index("uploaded_date")
    collection.create_index([("user_id", 1), ("uploaded_date", -1)])
    
    return collection


def get_images_collection():
    """Get images collection with indexes for extracted/uploaded images"""
    collection = db_connection.get_collection("images")
    
    # Create indexes for better performance
    collection.create_index("user_id")
    collection.create_index("document_id")
    collection.create_index("uploaded_date")
    collection.create_index("source_type")
    collection.create_index([("user_id", 1), ("source_type", 1)])
    collection.create_index([("document_id", 1), ("source_type", 1)])
    
    return collection



def get_single_annotations_collection():
    """Get single_annotations collection for single-image annotations"""
    collection = db_connection.get_collection("single_annotations")
    
    # Create indexes for better performance
    collection.create_index("user_id")
    collection.create_index("image_id")
    collection.create_index("created_at")
    collection.create_index([("user_id", 1), ("image_id", 1)])
    collection.create_index([("image_id", 1), ("created_at", -1)])
    
    return collection


def get_dual_annotations_collection():
    """Get dual_annotations collection for cross-image annotations"""
    collection = db_connection.get_collection("dual_annotations")
    
    # Create indexes for better performance
    collection.create_index("user_id")
    collection.create_index("source_image_id")  # Image where annotation is drawn
    collection.create_index("target_image_id")  # Linked target image
    collection.create_index("link_id")
    collection.create_index("created_at")
    collection.create_index([("user_id", 1), ("source_image_id", 1)])
    collection.create_index([("user_id", 1), ("target_image_id", 1)])
    collection.create_index([("user_id", 1), ("link_id", 1)])
    collection.create_index([("source_image_id", 1), ("target_image_id", 1)])
    
    return collection


def get_analyses_collection():
    """Get analyses collection with indexes for copy-move detection and analysis dashboard"""
    collection = db_connection.get_collection("analyses")
    
    # Create indexes for better performance
    collection.create_index("user_id")
    collection.create_index("source_image_id")
    collection.create_index("target_image_id")
    collection.create_index("type")
    collection.create_index("status")
    collection.create_index("created_at")
    # Compound indexes for common Analysis Dashboard queries
    collection.create_index([("user_id", 1), ("created_at", -1)])
    collection.create_index([("user_id", 1), ("type", 1), ("created_at", -1)])
    collection.create_index([("user_id", 1), ("status", 1), ("created_at", -1)])
    collection.create_index([("user_id", 1), ("source_image_id", 1)])
    
    return collection


def get_relationships_collection():
    """Get image_relationships collection for storing image-to-image relationships"""
    collection = db_connection.get_collection("image_relationships")
    
    # Create indexes for better performance
    collection.create_index("user_id")
    collection.create_index("image1_id")
    collection.create_index("image2_id")
    collection.create_index("source_type")
    collection.create_index("created_at")
    # Unique compound index to prevent duplicate relationships (IDs are normalized/sorted)
    collection.create_index(
        [("user_id", 1), ("image1_id", 1), ("image2_id", 1)],
        unique=True
    )
    # Query relationships for an image (check both directions)
    collection.create_index([("user_id", 1), ("image1_id", 1)])
    collection.create_index([("user_id", 1), ("image2_id", 1)])
    
    return collection


# Flag to track if indexing_jobs indexes have been created
_indexing_jobs_indexes_created = False


def get_indexing_jobs_collection():
    """Get indexing_jobs collection for tracking batch indexing progress"""
    global _indexing_jobs_indexes_created
    collection = db_connection.get_collection("indexing_jobs")
    
    # Create indexes only once (on first access)
    if not _indexing_jobs_indexes_created:
        collection.create_index("user_id", background=True)
        collection.create_index("status", background=True)
        collection.create_index("created_at", background=True)
        collection.create_index([("user_id", 1), ("created_at", -1)], background=True)
        _indexing_jobs_indexes_created = True
    
    return collection


# Flag to track if jobs indexes have been created
_jobs_indexes_created = False


def get_jobs_collection():
    """Get jobs collection for unified background job tracking with TTL expiration"""
    global _jobs_indexes_created
    collection = db_connection.get_collection("jobs")
    
    # Create indexes only once (on first access)
    if not _jobs_indexes_created:
        try:
            collection.create_index("user_id", background=True)
            collection.create_index("job_type", background=True)
            collection.create_index("status", background=True)
            collection.create_index("created_at", background=True)
            collection.create_index([("user_id", 1), ("created_at", -1)], background=True)
            collection.create_index([("user_id", 1), ("job_type", 1), ("created_at", -1)], background=True)
            # TTL index: auto-delete documents when expires_at timestamp passes
            collection.create_index("expires_at", expireAfterSeconds=0, background=True)
            _jobs_indexes_created = True
        except Exception as e:
            logger.warning(f"Error creating indexes for jobs collection: {e}")
    
    return collection


def get_database():
    """Get database instance"""
    return db_connection.get_database()

