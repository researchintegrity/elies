"""
CBIR (Content-Based Image Retrieval) Routes

Provides endpoints for image similarity search and indexing.
"""
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from typing import Optional, List
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId

from app.utils.security import get_current_user
from app.db.mongodb import get_images_collection, get_analyses_collection
from app.schemas import (
    CBIRIndexRequest,
    CBIRSearchRequest,
    CBIRSearchResponse,
    CBIRSearchResult,
    CBIRDeleteRequest,
    CBIRStatusResponse,
    AnalysisType,
    AnalysisStatus,
)
from app.utils.docker_cbir import (
    search_similar_images_upload,
)
from app.tasks.cbir import (
    cbir_index_image,
    cbir_index_batch,
    cbir_search,
    cbir_delete_image,
    cbir_delete_user_data,
)
from app.services.task_submission import submit_task
from app.services.cbir_service import (
    get_user_images_for_indexing,
    search_similar_by_image_id,
    enrich_search_results,
    get_cbir_status,
)

router = APIRouter(
    prefix="/cbir",
    tags=["CBIR - Image Similarity Search"]
)


@router.get("/health", response_model=CBIRStatusResponse)
def cbir_health():
    """
    Check CBIR service health status.
    
    Returns the health status of the CBIR microservice.
    """
    status_info = get_cbir_status()
    return CBIRStatusResponse(**status_info)


@router.post("/index", status_code=status.HTTP_202_ACCEPTED)
def index_images(
    request: CBIRIndexRequest = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Index images in the CBIR system for similarity search.
    
    If image_ids is provided, only those images are indexed.
    Otherwise, all user images are indexed.
    
    This operation runs asynchronously and returns immediately.
    """
    user_id = str(current_user["_id"])
    
    # Get images to index
    items = get_user_images_for_indexing(
        user_id=user_id,
        image_ids=request.image_ids if request else None,
        labels=request.labels if request else None
    )
    
    if not items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No images found to index"
        )
    
    # Get image IDs for the task
    images_col = get_images_collection()
    if request and request.image_ids:
        # Use provided IDs
        image_ids = request.image_ids
    else:
        # Get all user image IDs
        paths = [item["image_path"] for item in items]
        images = list(images_col.find(
            {"user_id": user_id, "file_path": {"$in": paths}},
            {"_id": 1, "file_path": 1}
        ))
        image_ids = [str(img["_id"]) for img in images]
        # Create path to ID mapping
        path_to_id = {img["file_path"]: str(img["_id"]) for img in images}
        # Update items with image_id
        for item in items:
            item["image_id"] = path_to_id.get(item["image_path"])
    
    # Prepare items with IDs
    if request and request.image_ids:
        # Add image_id to items
        images = list(images_col.find(
            {"_id": {"$in": [ObjectId(id) for id in image_ids]}, "user_id": user_id},
            {"_id": 1, "file_path": 1}
        ))
        id_to_path = {str(img["_id"]): img["file_path"] for img in images}
        items = [
            {
                "image_id": img_id,
                "image_path": id_to_path.get(img_id),
                "labels": request.labels or []
            }
            for img_id in image_ids if img_id in id_to_path
        ]
    else:
        # Items already have paths, add IDs
        pass
    
    # Trigger async indexing
    if len(items) == 1:
        cbir_index_image.delay(
            user_id=user_id,
            image_id=items[0]["image_id"],
            image_path=items[0]["image_path"],
            labels=items[0].get("labels", [])
        )
    else:
        cbir_index_batch.delay(user_id=user_id, image_items=items)
    
    return {
        "message": f"Indexing {len(items)} images",
        "image_count": len(items),
        "status": "processing"
    }


@router.post("/search", status_code=status.HTTP_202_ACCEPTED)
def search_similar(
    request: CBIRSearchRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Search for images similar to a query image.
    
    The search is asynchronous and creates an analysis record.
    Poll the analysis endpoint to get results.
    """
    user_id = str(current_user["_id"])
    
    # Verify the query image exists and belongs to user
    images_col = get_images_collection()
    try:
        query_image = images_col.find_one({
            "_id": ObjectId(request.image_id),
            "user_id": user_id
        })
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid image ID format"
        )
    
    if not query_image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Query image not found or access denied"
        )
    
    # Create analysis record
    analyses_col = get_analyses_collection()
    analysis_doc = {
        "type": AnalysisType.CBIR_SEARCH,
        "user_id": user_id,
        "source_image_id": request.image_id,
        "status": AnalysisStatus.PENDING,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "parameters": {
            "top_k": request.top_k,
            "labels": request.labels
        }
    }
    result = analyses_col.insert_one(analysis_doc)
    analysis_id = str(result.inserted_id)
    
    # Update image with analysis reference
    images_col.update_one(
        {"_id": ObjectId(request.image_id)},
        {"$addToSet": {"analysis_ids": analysis_id}}
    )
    
    # Trigger async search
    submit_task(cbir_search, dict(
        analysis_id=analysis_id,
        user_id=user_id,
        query_image_id=request.image_id,
        query_image_path=query_image["file_path"],
        top_k=request.top_k,
        labels=request.labels
    ), owner_id=user_id, analysis_id=analysis_id)
    
    return {
        "message": "CBIR search started",
        "analysis_id": analysis_id,
        "query_image_id": request.image_id,
        "top_k": request.top_k
    }


@router.post("/search/sync", response_model=CBIRSearchResponse)
def search_similar_sync(
    request: CBIRSearchRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Search for images similar to a query image (synchronous).
    
    This endpoint blocks until results are available.
    Use this for quick searches when you need immediate results.
    """
    user_id = str(current_user["_id"])
    
    # Verify the query image exists and belongs to user
    images_col = get_images_collection()
    try:
        query_image = images_col.find_one({
            "_id": ObjectId(request.image_id),
            "user_id": user_id
        })
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid image ID format"
        )
    
    if not query_image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Query image not found or access denied"
        )
    
    # Perform search
    success, message, results = search_similar_by_image_id(
        user_id=user_id,
        image_id=request.image_id,
        top_k=request.top_k,
        labels=request.labels
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=message
        )
    
    # Enrich results
    enriched = enrich_search_results(user_id, results)
    
    return CBIRSearchResponse(
        query_image_id=request.image_id,
        top_k=request.top_k,
        labels_filter=request.labels,
        matches_count=len(enriched),
        matches=[CBIRSearchResult(**r) for r in enriched]
    )


@router.post("/search/upload", response_model=CBIRSearchResponse)
def search_by_upload(
    file: UploadFile = File(...),
    top_k: int = Query(10, ge=1, le=100),
    labels: Optional[List[str]] = Query(None, max_length=50),
    current_user: dict = Depends(get_current_user)
):
    """
    Search for similar images by uploading an image directly.
    
    The uploaded image is not stored - it's only used for the search query.
    """
    user_id = str(current_user["_id"])
    
    # Read uploaded file
    image_data = file.file.read()
    
    # Perform search via upload endpoint
    success, message, results = search_similar_images_upload(
        user_id=user_id,
        image_data=image_data,
        filename=file.filename or "query.jpg",
        top_k=top_k,
        labels=labels
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=message
        )
    
    # Enrich results
    enriched = enrich_search_results(user_id, results)
    
    return CBIRSearchResponse(
        query_image_id="upload",
        top_k=top_k,
        labels_filter=labels,
        matches_count=len(enriched),
        matches=[CBIRSearchResult(**r) for r in enriched]
    )


@router.delete("/index", status_code=status.HTTP_202_ACCEPTED)
def delete_from_index(
    request: CBIRDeleteRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Remove images from the CBIR index.
    
    The images are not deleted from storage, only from the similarity search index.
    """
    user_id = str(current_user["_id"])
    
    # Verify all images belong to user
    images_col = get_images_collection()
    images = list(images_col.find({
        "_id": {"$in": [ObjectId(id) for id in request.image_ids]},
        "user_id": user_id
    }))
    
    if len(images) != len(request.image_ids):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Some images not found or access denied"
        )
    
    # Trigger async deletion for each image
    for img in images:
        cbir_delete_image.delay(
            user_id=user_id,
            image_id=str(img["_id"]),
            image_path=img["file_path"]
        )
    
    return {
        "message": f"Removing {len(images)} images from index",
        "image_count": len(images),
        "status": "processing"
    }


@router.delete("/index/all", status_code=status.HTTP_202_ACCEPTED)
def delete_all_from_index(
    current_user: dict = Depends(get_current_user)
):
    """
    Remove all user's images from the CBIR index.
    
    This clears the entire similarity search index for the current user.
    The images themselves are not deleted from storage.
    """
    user_id = str(current_user["_id"])
    
    # Trigger async deletion
    cbir_delete_user_data.delay(user_id=user_id)
    
    return {
        "message": "Removing all images from index",
        "status": "processing"
    }
