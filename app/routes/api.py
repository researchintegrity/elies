"""
Frontend-specific API routes for dashboard, documents, and images
Provides unified endpoints with pagination, filtering, and standardized responses
"""

import re

from fastapi import APIRouter, Query, Depends
from datetime import datetime
from typing import Optional
from app.schemas import ApiResponse, PaginatedResponse
from app.config.storage_quota import DEFAULT_USER_STORAGE_QUOTA
from app.db.mongodb import get_documents_collection, get_images_collection
from app.utils.security import get_current_user
from app.services.document_service import delete_document_and_artifacts
from app.services.image_service import delete_image_and_artifacts, list_images as list_images_service
from app.services.resource_helpers import get_owned_resource

router = APIRouter(prefix="/api", tags=["api"])


# ============================================================================
# HEALTH CHECK
# ============================================================================

@router.get(
    "/health",
    response_model=ApiResponse,
    summary="Health Check",
    description="Check if the API is running and responsive"
)
def health_check():
    """
    Health check endpoint to verify API is operational
    """
    return ApiResponse(
        success=True,
        message="API is healthy and operational",
        data={"status": "healthy", "timestamp": datetime.utcnow().isoformat()}
    )


# ============================================================================
# DASHBOARD ENDPOINTS
# ============================================================================

@router.get(
    "/dashboard/stats",
    response_model=ApiResponse,
    summary="Get Dashboard Statistics",
    description="Retrieve overall system statistics for the dashboard"
)
def get_dashboard_stats(current_user: dict = Depends(get_current_user)):
    """
    Get dashboard statistics including document count, image count, and storage usage
    
    Args:
        current_user: Currently authenticated user
        
    Returns:
        Dashboard statistics with success status
    """
    user_id = str(current_user.get("_id"))
    
    # Get document count
    doc_collection = get_documents_collection()
    doc_count = doc_collection.count_documents({"user_id": user_id})
    
    # Get image count
    img_collection = get_images_collection()
    img_count = img_collection.count_documents({"user_id": user_id})
    
    # Storage figures come from the user record (kept current on every upload/delete)
    storage_used = current_user.get("storage_used_bytes", 0)
    storage_limit = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
    storage_remaining = max(0, storage_limit - storage_used)
    
    return ApiResponse(
        success=True,
        message="Dashboard statistics retrieved successfully",
        data={
            "total_documents": doc_count,
            "total_images": img_count,
            "storage_used": storage_used,
            "storage_limit": storage_limit,
            "storage_remaining": storage_remaining,
            "storage_percent_used": round((storage_used / storage_limit) * 100, 2) if storage_limit else 0.0
        }
    )


# ============================================================================
# DOCUMENTS ENDPOINTS
# ============================================================================

@router.get(
    "/documents",
    response_model=PaginatedResponse,
    summary="List User Documents",
    description="Retrieve paginated list of user's documents with optional filtering"
)
def list_documents(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number starting from 1"),
    per_page: int = Query(10, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("uploaded_date", pattern="^(uploaded_date|filename|file_size)$", description="Sort field: uploaded_date, filename, file_size"),
    order: str = Query("desc", description="Sort order: asc or desc"),
    search: Optional[str] = Query(None, description="Search in filename")
):
    """
    List documents with pagination and filtering
    
    Args:
        current_user: Currently authenticated user
        page: Page number (1-indexed)
        per_page: Items per page (1-100)
        sort_by: Field to sort by
        order: Sort order (asc/desc)
        search: Optional search term for filename
        
    Returns:
        Paginated list of documents
    """
    user_id = str(current_user.get("_id"))
    collection = get_documents_collection()
    
    # Build filter
    filter_query = {"user_id": user_id}
    if search:
        filter_query["filename"] = {"$regex": re.escape(search), "$options": "i"}
    
    # Get total count
    total_items = collection.count_documents(filter_query)
    total_pages = (total_items + per_page - 1) // per_page
    
    # Validate pagination
    if page > total_pages and total_pages > 0:
        page = total_pages
    
    # Calculate skip
    skip = (page - 1) * per_page
    
    # Build sort order
    sort_order = -1 if order.lower() == "desc" else 1
    
    # Query documents
    documents = list(collection.find(filter_query)
                    .sort(sort_by, sort_order)
                    .skip(skip)
                    .limit(per_page))
    
    # Convert ObjectId to string for JSON serialization
    for doc in documents:
        doc["_id"] = str(doc["_id"])
        if "user_id" in doc:
            doc["user_id"] = str(doc["user_id"])
    
    return PaginatedResponse(
        success=True,
        message="Documents retrieved successfully",
        data=documents,
        pagination={
            "current_page": page,
            "total_pages": total_pages,
            "page_size": per_page,
            "total_items": total_items
        }
    )


@router.get(
    "/documents/{document_id}",
    response_model=ApiResponse,
    summary="Get Document Details",
    description="Retrieve detailed information about a specific document"
)
def get_document_detail(
    document_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get detailed information about a specific document including associated images
    
    Args:
        document_id: Document ID
        current_user: Currently authenticated user
        
    Returns:
        Document details with associated images
    """
    user_id = str(current_user.get("_id"))
    
    # Get document with ownership validation
    document = get_owned_resource(
        get_documents_collection,
        document_id,
        user_id,
        "Document"
    )
    
    # Convert ObjectId to string
    document["_id"] = str(document["_id"])
    
    # Get associated images
    img_collection = get_images_collection()
    images = list(img_collection.find({"document_id": document_id, "user_id": user_id}, {"exif_metadata": 0}))
    for img in images:
        img["_id"] = str(img["_id"])
    
    document["associated_images"] = images
    
    return ApiResponse(
        success=True,
        message="Document retrieved successfully",
        data=document
    )


@router.delete(
    "/documents/{document_id}",
    response_model=ApiResponse,
    summary="Delete Document",
    description="Delete a document and its associated images and annotations"
)
def delete_document(
    document_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a document and all its associated images and annotations
    
    Delegates to the document service which handles all cleanup:
    - PDF file deletion from disk
    - Extraction directory deletion
    - Annotation deletion (cascade)
    - Image deletion from MongoDB
    - Document record deletion
    - Storage quota update
    
    Args:
        document_id: Document ID to delete
        current_user: Currently authenticated user
        
    Returns:
        Success message with deletion statistics
    """
    result = delete_document_and_artifacts(
        document_id=document_id,
        user_id=str(current_user.get("_id"))
    )
    
    return ApiResponse(
        success=True,
        message="Document and associated images deleted successfully",
        data=result
    )


# ============================================================================
# IMAGES ENDPOINTS
# ============================================================================

@router.get(
    "/images",
    response_model=PaginatedResponse,
    summary="List User Images",
    description="Retrieve paginated list of user's images with optional filtering"
)
def list_images(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number starting from 1"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("uploaded_date", pattern="^(uploaded_date|filename|file_size|source_type)$", description="Sort field"),
    order: str = Query("desc", description="Sort order: asc or desc"),
    source_type: Optional[str] = Query(None, description="Filter by source type: uploaded, extracted"),
    document_id: Optional[str] = Query(None, description="Filter by document ID"),
    image_type: Optional[str] = Query(None, description="Comma-separated list of image types/tags to filter"),
    date_from: Optional[str] = Query(None, description="Filter images from this date (ISO format YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter images until this date (ISO format YYYY-MM-DD)"),
    search: Optional[str] = Query(None, description="Search in filename (case-insensitive)")
):
    """
    List images with pagination and filtering
    
    Args:
        current_user: Currently authenticated user
        page: Page number (1-indexed)
        per_page: Items per page (1-100)
        sort_by: Field to sort by
        order: Sort order (asc/desc)
        source_type: Optional filter by source type
        document_id: Optional filter by document ID
        image_type: Optional comma-separated list of image types/tags
        date_from: Optional ISO date string for start of date range
        date_to: Optional ISO date string for end of date range
        search: Optional search string for filename
        
    Returns:
        Paginated list of images
    """
    user_id = str(current_user.get("_id"))
    
    # Build sort order
    sort_order = -1 if order.lower() == "desc" else 1
    
    # Parse image_type from comma-separated string
    parsed_image_type = [t.strip() for t in image_type.split(",")] if image_type else None
    
    # Use service to get images (get all to calculate pagination)
    result = list_images_service(
        user_id=user_id,
        source_type=source_type,
        document_id=document_id,
        image_type=parsed_image_type,
        date_from=date_from,
        date_to=date_to,
        search=search,
        limit=per_page,
        offset=(page - 1) * per_page,
        sort_by=sort_by,
        sort_order=sort_order
    )
    
    # Calculate pagination
    total_items = result["total"]
    total_pages = (total_items + per_page - 1) // per_page
    
    # Validate page
    if page > total_pages and total_pages > 0:
        page = total_pages
    
    return PaginatedResponse(
        success=True,
        message="Images retrieved successfully",
        data=result["images"],
        pagination={
            "current_page": page,
            "total_pages": total_pages,
            "page_size": per_page,
            "total_items": total_items
        }
    )


@router.get(
    "/images/{image_id}",
    response_model=ApiResponse,
    summary="Get Image Details",
    description="Retrieve detailed information about a specific image"
)
def get_image_detail(
    image_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get detailed information about a specific image
    
    Args:
        image_id: Image ID
        current_user: Currently authenticated user
        
    Returns:
        Image details
    """
    user_id = str(current_user.get("_id"))
    image = get_owned_resource(get_images_collection, image_id, user_id, "Image")
    
    # Convert ObjectId to string
    image["_id"] = str(image["_id"])
    
    return ApiResponse(
        success=True,
        message="Image retrieved successfully",
        data=image
    )


@router.delete(
    "/images/{image_id}",
    response_model=ApiResponse,
    summary="Delete Image",
    description="Delete a specific image"
)
def delete_image(
    image_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a specific image
    
    Args:
        image_id: Image ID to delete
        current_user: Currently authenticated user
        
    Returns:
        Success message
    """
    result = delete_image_and_artifacts(
        image_id=image_id,
        user_id=str(current_user.get("_id"))
    )
    
    return ApiResponse(
        success=True,
        message="Image deleted successfully",
        data=result
    )


# ============================================================================
# SEARCH ENDPOINTS
# ============================================================================

@router.get(
    "/search",
    response_model=PaginatedResponse,
    summary="Global Search",
    description="Search across documents and images"
)
def global_search(
    query: str = Query(..., min_length=1, max_length=200, description="Search query"),
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, le=1000, description="Page number starting from 1"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page")
):
    """
    Global search across documents and images
    
    Args:
        query: Search query string
        current_user: Currently authenticated user
        page: Page number (1-indexed)
        per_page: Items per page (1-100)
        
    Returns:
        Paginated search results
    """
    user_id = str(current_user.get("_id"))
    name_filter = {"user_id": user_id, "filename": {"$regex": re.escape(query), "$options": "i"}}
    projection = {"exif_metadata": 0, "extracted_images": 0}
    skip = (page - 1) * per_page

    # Fetch only the newest page*per_page matches of each kind, then merge
    doc_collection = get_documents_collection()
    img_collection = get_images_collection()
    documents = list(doc_collection.find(name_filter, projection).sort("uploaded_date", -1).limit(skip + per_page))
    images = list(img_collection.find(name_filter, projection).sort("uploaded_date", -1).limit(skip + per_page))
    total_items = doc_collection.count_documents(name_filter) + img_collection.count_documents(name_filter)

    results = []
    for doc in documents:
        doc["_id"] = str(doc["_id"])
        doc["type"] = "document"
        results.append(doc)
    for img in images:
        img["_id"] = str(img["_id"])
        img["type"] = "image"
        results.append(img)

    results.sort(key=lambda x: x.get("uploaded_date") or datetime.min, reverse=True)
    paginated_results = results[skip:skip + per_page]
    total_pages = (total_items + per_page - 1) // per_page

    return PaginatedResponse(
        success=True,
        message=f"Found {total_items} results for '{query}'",
        data=paginated_results,
        pagination={
            "current_page": page,
            "total_pages": total_pages,
            "page_size": per_page,
            "total_items": total_items
        }
    )


