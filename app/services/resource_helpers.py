"""
Resource helper functions for common validation and resource retrieval patterns.

Eliminates code duplication across routes for resource ownership validation.
Raises domain exceptions that are auto-converted to HTTP by FastAPI handlers.
"""
from typing import Any, Callable, Dict

from bson import ObjectId

from app.exceptions import ResourceNotFoundError, ValidationError


def get_owned_resource(
    collection_getter: Callable,
    resource_id: str,
    user_id: str,
    resource_name: str = "Resource"
) -> Dict[str, Any]:
    """
    Retrieve a resource from a collection while verifying it belongs to the user.
    
    This is a single source of truth for resource ownership validation.
    Called by routes to eliminate duplicate ObjectId validation and ownership checks.
    
    Args:
        collection_getter: Function that returns the MongoDB collection.
        resource_id: Resource ID to retrieve (as string).
        user_id: User ID (as string) who should own the resource.
        resource_name: Human-readable name for error messages.
        
    Returns:
        Document dictionary from MongoDB.
        
    Raises:
        ValidationError: If resource_id is not a valid ObjectId format.
        ResourceNotFoundError: If resource not found or doesn't belong to user.
    """
    # Validate ObjectId format
    try:
        resource_oid = ObjectId(resource_id)
    except Exception:
        raise ValidationError(f"Invalid {resource_name.lower()} ID format")
    
    # Retrieve resource with ownership check
    collection = collection_getter()
    resource = collection.find_one({
        "_id": resource_oid,
        "user_id": user_id
    })
    
    if not resource:
        raise ResourceNotFoundError(
            resource_name, 
            resource_id,
            f"{resource_name} not found or doesn't belong to you"
        )
    
    return resource


