from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from app.utils.security import get_current_user
from app.db.mongodb import get_analyses_collection, get_images_collection
from app.schemas import (
    AnalysisResponse,
    CrossImageAnalysisCreate,
    SingleImageAnalysisCreate,
    TruForAnalysisCreate,
    ScreeningToolAnalysisCreate,
    AnalysisType,
    AnalysisStatus,
    PaginatedResponse,
    JobType,
)
from app.services.resource_helpers import get_owned_resource
from app.services.job_logger import create_job_log
from app.config.settings import convert_container_path_to_host, is_container_path
from datetime import datetime
from bson import ObjectId
from pathlib import Path
from typing import Optional
import os
from app.tasks.copy_move_detection import detect_copy_move

router = APIRouter(
    prefix="/analyses",
    tags=["Analyses"]
)


@router.get("/stats", response_model=dict)
async def get_analysis_stats(
    current_user: dict = Depends(get_current_user)
):
    """
    Get analysis statistics for the current user.
    
    Returns total counts by status for all analyses.
    This is used to power the stats badges in the Analysis Dashboard.
    """
    try:
        user_id_str = str(current_user["_id"])
        analyses_col = get_analyses_collection()
        
        # Count by status using aggregation
        pipeline = [
            {"$match": {"user_id": user_id_str}},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]
        
        status_counts = list(analyses_col.aggregate(pipeline))
        
        # Build response with all statuses
        stats = {
            "total": 0,
            "completed": 0,
            "processing": 0,
            "pending": 0,
            "failed": 0
        }
        
        for item in status_counts:
            status_key = item["_id"]
            if status_key in stats:
                stats[status_key] = item["count"]
            stats["total"] += item["count"]
        
        return {
            "success": True,
            "message": "Stats retrieved successfully",
            "data": stats
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve analysis stats: {str(e)}"
        )


@router.get("", response_model=PaginatedResponse)
def list_analyses(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number starting from 1"),
    per_page: int = Query(10, ge=1, le=100, description="Items per page"),
    type: Optional[AnalysisType] = Query(None, description="Filter by analysis type"),
    status: Optional[AnalysisStatus] = Query(None, description="Filter by analysis status"),
    source_image_id: Optional[str] = Query(None, description="Filter by source image ID"),
    date_from: Optional[datetime] = Query(None, description="Filter analyses created after this date"),
    date_to: Optional[datetime] = Query(None, description="Filter analyses created before this date"),
    sort_by: str = Query("created_at", description="Sort field: created_at, updated_at, type, status"),
    order: str = Query("desc", description="Sort order: asc or desc")
):
    """
    List analyses with pagination and filtering.
    
    Returns a paginated list of all analyses for the current user with optional filters.
    Use this endpoint to power the Analysis Dashboard.
    
    Args:
        page: Page number (1-indexed)
        per_page: Items per page (1-100)
        type: Filter by analysis type (single_image_copy_move, cross_image_copy_move, trufor, etc.)
        status: Filter by analysis status (pending, processing, completed, failed)
        source_image_id: Filter by source image
        date_from: Filter analyses created on or after this date
        date_to: Filter analyses created on or before this date
        sort_by: Field to sort by
        order: Sort order (asc/desc)
        
    Returns:
        Paginated list of analyses with parameters field for reproducibility
    """
    try:
        user_id_str = str(current_user["_id"])
        analyses_col = get_analyses_collection()
        
        # Build filter query - always filter by user_id for security
        filter_query = {"user_id": user_id_str}
        
        # Exclude non-forensic analysis types from the Analysis Dashboard
        # These belong in the Jobs dashboard or other views
        EXCLUDED_TYPES = ["cbir_search", "document_extraction", "image_extraction"]
        
        # Valid forensic screening tool subtypes (exclude records that don't match these)
        VALID_SCREENING_SUBTYPES = ["ela", "noise", "gradient", "levelSweep", "cloneDetection", "metadata"]
        
        if type:
            # If user explicitly filters by type, validate that it's allowed in this view  
            if type.value in EXCLUDED_TYPES:  
                raise HTTPException(  
                    status_code=status.HTTP_400_BAD_REQUEST,  
                    detail=f"Analysis type '{type.value}' is not available in this view."  
                )  
            filter_query["type"] = type.value  
            # If filtering by screening_tool, also require valid subtype  
            if type.value == "screening_tool":  
                filter_query["parameters.analysis_subtype"] = {"$in": VALID_SCREENING_SUBTYPES}  
        else:
            # By default, exclude non-forensic types AND mislabeled screening_tool records
            # Use $or to include:
            # 1. All forensic types except screening_tool
            # 2. screening_tool with valid subtypes only
            def get_dashboard_type_filter(EXCLUDED_TYPES, VALID_SCREENING_SUBTYPES):
                """
                Returns query to match only dashboard-appropriate forensic analyses:
                1. Include standard forensic types (excluding non-forensic & generic screening tools)
                2. Include screening tools only if they match valid forensic subtypes
                """
                return {
                    "$or": [
                        {"type": {"$nin": EXCLUDED_TYPES + ["screening_tool"]}},
                        {
                            "type": "screening_tool",
                            "parameters.analysis_subtype": {"$in": VALID_SCREENING_SUBTYPES}
                        }
                    ]
                }

            filter_query.update(get_dashboard_type_filter(EXCLUDED_TYPES, VALID_SCREENING_SUBTYPES))
        
        if status:
            filter_query["status"] = status.value
            
        if source_image_id:
            filter_query["source_image_id"] = source_image_id
            
        # Date range filters
        if date_from or date_to:
            date_filter = {}
            if date_from:
                date_filter["$gte"] = date_from
            if date_to:
                date_filter["$lte"] = date_to
            filter_query["created_at"] = date_filter
        
        # Get total count for pagination
        total_items = analyses_col.count_documents(filter_query)
        total_pages = (total_items + per_page - 1) // per_page if total_items > 0 else 1
        
        # Validate pagination
        if page > total_pages and total_pages > 0:
            page = total_pages
        
        # Calculate skip
        skip = (page - 1) * per_page
        
        # Build sort order
        sort_order = -1 if order.lower() == "desc" else 1
        valid_sort_fields = {"created_at", "updated_at", "type", "status"}
        if sort_by not in valid_sort_fields:
            sort_by = "created_at"
        
        # Query analyses
        analyses = list(analyses_col.find(filter_query)
                       .sort(sort_by, sort_order)
                       .skip(skip)
                       .limit(per_page))
        
        # Convert ObjectId to string for JSON serialization
        for analysis in analyses:
            analysis["_id"] = str(analysis["_id"])
        
        return PaginatedResponse(
            success=True,
            message="Analyses retrieved successfully",
            data=analyses,
            pagination={
                "current_page": page,
                "total_pages": total_pages,
                "page_size": per_page,
                "total_items": total_items
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve analyses: {str(e)}"
        )


@router.get("/by-image/{image_id}", response_model=list)
async def list_analyses_by_image(
    image_id: str,
    current_user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of analyses to return"),
):
    """
    Get all analyses associated with a specific image.
    
    Returns analyses where this image is either the source or target image.
    Useful for showing the complete analysis history of a flagged image.
    
    Args:
        image_id: The image ID to find analyses for
        current_user: Current authenticated user
        limit: Maximum number of analyses to return
        
    Returns:
        List of analyses sorted by creation date (newest first)
    """
    try:
        user_id_str = str(current_user["_id"])
        analyses_col = get_analyses_collection()
        
        # Find analyses where this image is source or target
        filter_query = {
            "user_id": user_id_str,
            "$or": [
                {"source_image_id": image_id},
                {"target_image_id": image_id}
            ]
        }
        
        # Query analyses, sorted by newest first
        analyses = list(
            analyses_col.find(filter_query)
            .sort("created_at", -1)
            .limit(limit)
        )
        
        # Convert ObjectId to string for JSON serialization
        for analysis in analyses:
            analysis["_id"] = str(analysis["_id"])
        
        return analyses
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve analyses for image: {str(e)}"
        )


@router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get analysis details by ID.
    """
    user_id_str = str(current_user["_id"])
    analyses_col = get_analyses_collection()
    
    analysis = analyses_col.find_one({"_id": ObjectId(analysis_id)})
    
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found"
        )
        
    if analysis["user_id"] != user_id_str:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this analysis"
        )
        
    return analysis


@router.delete("/{analysis_id}", status_code=status.HTTP_200_OK)
async def delete_analysis(
    analysis_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete an analysis by ID.
    
    This will:
    - Remove the analysis document from the database
    - Remove the analysis_id reference from associated images
    - Optionally clean up result files (if they exist)
    
    Args:
        analysis_id: The ID of the analysis to delete
        
    Returns:
        Success message
    """
    user_id_str = str(current_user["_id"])
    analyses_col = get_analyses_collection()
    
    # Find the analysis first to verify it exists and user owns it
    try:
        analysis = analyses_col.find_one({"_id": ObjectId(analysis_id)})
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid analysis ID format"
        )
    
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found"
        )
        
    if analysis["user_id"] != user_id_str:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this analysis"
        )
    
    # Get image IDs to update (remove analysis reference)
    image_ids = []
    if analysis.get("source_image_id"):
        image_ids.append(analysis["source_image_id"])
    if analysis.get("target_image_id"):
        image_ids.append(analysis["target_image_id"])
    
    # Remove analysis reference from associated images
    if image_ids:
        images_col = get_images_collection()
        for img_id in image_ids:
            try:
                images_col.update_one(
                    {"_id": ObjectId(img_id)},
                    {"$pull": {"analysis_ids": analysis_id}}
                )
            except Exception:
                pass  # Image may have been deleted, continue
    
    # Clean up result files and the analysis folder
    results = analysis.get("results", {})
    analysis_dirs_to_remove = set()
    
    # First, identify and remove individual result files
    for key, value in results.items():
        if isinstance(value, str) and os.path.isfile(value):
            try:
                # Track the parent directory (analysis folder)
                parent_dir = os.path.dirname(value)
                analysis_dirs_to_remove.add(parent_dir)
                os.remove(value)
            except Exception:
                pass  # File may not exist or be inaccessible
    
    # Now remove the analysis folder(s) if they're empty or contain only this analysis's files
    import shutil
    for analysis_dir in analysis_dirs_to_remove:
        if os.path.isdir(analysis_dir):
            try:
                # Check if the directory name matches the analysis_id (safety check)
                if analysis_id in analysis_dir:
                    shutil.rmtree(analysis_dir)
            except Exception:
                pass  # Directory may not exist or be inaccessible
    
    # Delete the analysis document
    analyses_col.delete_one({"_id": ObjectId(analysis_id)})
    
    return {
        "success": True,
        "message": f"Analysis {analysis_id} deleted successfully"
    }


@router.get("/{analysis_id}/results/{result_type}/download")
async def download_analysis_result(
    analysis_id: str,
    result_type: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Download an analysis result image file.
    
    Args:
        analysis_id: Analysis ID
        result_type: Type of result to download:
            - Copy-move: 'matches', 'clusters'
            - TruFor: 'pred_map', 'conf_map', 'noiseprint'
            - Screening Tool: 'result_image'
        current_user: Current authenticated user
        
    Returns:
        FileResponse with the result image
    """
    user_id_str = str(current_user["_id"])
    
    # Validate result_type - support copy-move, trufor, and screening tool result types
    valid_types = ("matches", "clusters", "pred_map", "conf_map", "noiseprint", "result_image")
    if result_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid result type. Must be one of: {', '.join(valid_types)}"
        )
    
    analyses_col = get_analyses_collection()
    analysis = analyses_col.find_one({"_id": ObjectId(analysis_id)})
    
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found"
        )
        
    if analysis["user_id"] != user_id_str:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this analysis"
        )
    
    # Get the result file path
    results = analysis.get("results", {})
    
    # Map result type to the correct key in results
    if result_type in ("pred_map", "conf_map", "noiseprint"):
        # TruFor uses 'pred_map', 'conf_map', and 'noiseprint' keys directly
        file_path = results.get(result_type)
        
        # Fallback: check 'files' array if the specific key is not set
        # (happens when there's a filename mismatch in TruFor output)
        if not file_path:
            files = results.get("files", [])
            if files and len(files) > 0:
                # Try to find matching file
                for f in files:
                    if result_type == "pred_map" and "_pred_map" in f:
                        file_path = f
                        break
                    elif result_type == "conf_map" and "_conf_map" in f:
                        file_path = f
                        break
                    elif result_type == "noiseprint" and "_noiseprint" in f:
                        file_path = f
                        break
    elif result_type == "result_image":
        # Screening tool analysis uses 'result_image' key directly
        file_path = results.get(result_type)
    else:
        # Copy-move uses '{type}_image' keys
        result_key = f"{result_type}_image"
        file_path = results.get(result_key)
    
    if not file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {result_type} result available for this analysis"
        )
    
    # Check if file exists (path is already container path, which is mounted)
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Result file not found on disk: {file_path}"
        )
    
    # Return the file
    return FileResponse(
        path=file_path,
        filename=os.path.basename(file_path),
        media_type="image/png"
    )


@router.post("/copy-move/single", status_code=status.HTTP_202_ACCEPTED, response_model=dict)
async def analyze_copy_move_single(
    request: SingleImageAnalysisCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Start single-image copy-move detection analysis.
    
    Supports two detection methods:
    - 'keypoint': Advanced keypoint-based detection (recommended)
    - 'dense': Block-based dense matching
    """
    user_id_str = str(current_user["_id"])
    
    # Verify ownership
    image = await get_owned_resource(
        get_images_collection,
        request.image_id,
        user_id_str,
        "Image"
    )
    
    # Build parameters dictionary for reproducibility
    parameters = {
        "method": request.method.value,
        "dense_method": request.dense_method if request.method.value == "dense" else None
    }
    
    # Create Analysis document
    analyses_col = get_analyses_collection()
    analysis_doc = {
        "type": AnalysisType.SINGLE_IMAGE_COPY_MOVE,
        "user_id": user_id_str,
        "source_image_id": request.image_id,
        "status": AnalysisStatus.PENDING,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "parameters": parameters,
        # Keep legacy fields for backward compatibility
        "method": request.method.value,
        "dense_method": request.dense_method if request.method.value == "dense" else None
    }
    result = analyses_col.insert_one(analysis_doc)
    analysis_id = str(result.inserted_id)
    
    # Create job log entry for the jobs dashboard (pending state)
    job_id = create_job_log(
        user_id=user_id_str,
        job_type=JobType.COPY_MOVE_SINGLE,
        title="Copy-Move Detection (Single Image)",
        input_data={"image_id": request.image_id, "analysis_id": analysis_id, "method": request.method.value}
    )
    
    # Update Image document with analysis_id
    images_col = get_images_collection()
    images_col.update_one(
        {"_id": ObjectId(request.image_id)},
        {"$addToSet": {"analysis_ids": analysis_id}}
    )
    
    # Trigger task with analysis_id and job_id
    detect_copy_move.delay(
        analysis_id=analysis_id,
        image_id=request.image_id,
        user_id=user_id_str,
        image_path=image["file_path"],
        method=request.method.value,
        dense_method=request.dense_method,
        job_id=job_id
    )
    
    return {
        "message": "Single-image copy-move analysis started",
        "analysis_id": analysis_id
    }


@router.post("/copy-move/cross", status_code=status.HTTP_202_ACCEPTED, response_model=dict)
async def analyze_copy_move_cross(
    request: CrossImageAnalysisCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Start cross-image copy-move detection analysis.
    
    Detects if content from source image was copied to target image.
    
    Supports two detection methods:
    - 'keypoint': Advanced keypoint-based detection (recommended for cross-image)
      - Supports descriptor types: cv_sift, cv_rsift (default), vlfeat_sift_heq
    - 'dense': Block-based dense matching (methods 1-5)
    """
    user_id_str = str(current_user["_id"])
    
    # Verify ownership of both images
    source_image = await get_owned_resource(
        get_images_collection,
        request.source_image_id,
        user_id_str,
        "Source Image"
    )
    
    target_image = await get_owned_resource(
        get_images_collection,
        request.target_image_id,
        user_id_str,
        "Target Image"
    )
    
    # Build parameters dictionary for reproducibility
    parameters = {
        "method": request.method.value,
        "dense_method": request.dense_method if request.method.value == "dense" else None,
        "descriptor": request.descriptor.value if request.method.value == "keypoint" else None
    }
    
    # Create Analysis document
    analyses_col = get_analyses_collection()
    analysis_doc = {
        "type": AnalysisType.CROSS_IMAGE_COPY_MOVE,
        "user_id": user_id_str,
        "source_image_id": request.source_image_id,
        "target_image_id": request.target_image_id,
        "status": AnalysisStatus.PENDING,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "parameters": parameters,
        # Keep legacy fields for backward compatibility
        "method": request.method.value,
        "dense_method": request.dense_method if request.method.value == "dense" else None,
        "descriptor": request.descriptor.value if request.method.value == "keypoint" else None
    }
    result = analyses_col.insert_one(analysis_doc)
    analysis_id = str(result.inserted_id)
    
    # Create job log entry for the jobs dashboard (pending state)
    job_id = create_job_log(
        user_id=user_id_str,
        job_type=JobType.COPY_MOVE_CROSS,
        title="Copy-Move Detection (Cross Image)",
        input_data={
            "source_image_id": request.source_image_id,
            "target_image_id": request.target_image_id,
            "analysis_id": analysis_id,
            "method": request.method.value
        }
    )
    
    # Update both Image documents with analysis_id
    images_col = get_images_collection()
    images_col.update_many(
        {"_id": {"$in": [ObjectId(request.source_image_id), ObjectId(request.target_image_id)]}},
        {"$addToSet": {"analysis_ids": analysis_id}}
    )
    
    from app.tasks.copy_move_detection import detect_copy_move_cross
    
    detect_copy_move_cross.delay(
        analysis_id=analysis_id,
        source_image_id=request.source_image_id,
        target_image_id=request.target_image_id,
        user_id=user_id_str,
        source_image_path=source_image["file_path"],
        target_image_path=target_image["file_path"],
        method=request.method.value,
        dense_method=request.dense_method,
        descriptor=request.descriptor.value,
        job_id=job_id
    )
    
    return {
        "message": "Cross-image copy-move analysis started",
        "analysis_id": analysis_id
    }

@router.post("/trufor", status_code=status.HTTP_202_ACCEPTED, response_model=dict)
async def analyze_trufor(
    request: TruForAnalysisCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Start TruFor forgery detection analysis.
    
    Args:
        request.image_id: ID of the image to analyze
        request.save_noiseprint: Whether to save the noiseprint map (default: False)
    """
    user_id_str = str(current_user["_id"])
    
    # Verify ownership
    image = await get_owned_resource(
        get_images_collection,
        request.image_id,
        user_id_str,
        "Image"
    )
    
    # Build parameters dictionary for reproducibility
    parameters = {
        "save_noiseprint": request.save_noiseprint
    }
    
    # Create Analysis document
    analyses_col = get_analyses_collection()
    analysis_doc = {
        "type": AnalysisType.TRUFOR,
        "user_id": user_id_str,
        "source_image_id": request.image_id,
        "status": AnalysisStatus.PENDING,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "parameters": parameters
    }
    result = analyses_col.insert_one(analysis_doc)
    analysis_id = str(result.inserted_id)
    
    # Create job log entry for the jobs dashboard (pending state)
    job_id = create_job_log(
        user_id=user_id_str,
        job_type=JobType.TRUFOR,
        title="TruFor Forgery Detection",
        input_data={"image_id": request.image_id, "analysis_id": analysis_id, "save_noiseprint": request.save_noiseprint}
    )
    
    # Update Image document
    images_col = get_images_collection()
    images_col.update_one(
        {"_id": ObjectId(request.image_id)},
        {"$addToSet": {"analysis_ids": analysis_id}}
    )
    
    # Trigger task
    from app.tasks.trufor import detect_trufor
    detect_trufor.delay(
        analysis_id=analysis_id,
        image_id=request.image_id,
        user_id=user_id_str,
        image_path=image["file_path"],
        save_noiseprint=request.save_noiseprint,
        job_id=job_id
    )
    
    return {"message": "TruFor analysis started", "analysis_id": analysis_id}


@router.post("/screening-tool", status_code=status.HTTP_201_CREATED, response_model=AnalysisResponse)
async def save_screening_tool_analysis(
    image_id: str = Form(..., description="ID of the image that was analyzed"),
    analysis_subtype: str = Form(..., description="Subtype of analysis (e.g., 'ela', 'noise_analysis', 'magnifier')"),
    parameters: str = Form("{}", description="JSON string of parameters used in the analysis"),
    notes: Optional[str] = Form(None, description="Optional notes about the analysis"),
    result_image: Optional[UploadFile] = File(None, description="Optional result image file"),
    current_user: dict = Depends(get_current_user)
):
    """
    Save a screening tool/client-side analysis result.
    
    This endpoint allows client-side screening tools (like ELA, Noise Analysis, Magnifier, Histogram)
    to save their analysis results to the database for reproducibility and record-keeping.
    
    Args:
        image_id: ID of the image that was analyzed
        analysis_subtype: Type of analysis performed (e.g., 'ela', 'noise_analysis', 'magnifier')
        parameters: JSON string of parameters used (e.g., '{"quality": 90}' for ELA)
        notes: Optional notes or observations about the analysis
        result_image: Optional result image file to store
        
    Returns:
        The created analysis document
    """
    import json
    from app.utils.file_storage import get_analysis_output_path
    
    user_id_str = str(current_user["_id"])
    
    # Verify ownership of the image
    await get_owned_resource(
        get_images_collection,
        image_id,
        user_id_str,
        "Image"
    )
    
    # Parse parameters JSON
    try:
        params_dict = json.loads(parameters)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON in parameters field"
        )
    
    # Add subtype to parameters for clarity
    params_dict["analysis_subtype"] = analysis_subtype
    
    # Create analysis document
    analyses_col = get_analyses_collection()
    analysis_doc = {
        "type": AnalysisType.SCREENING_TOOL,
        "user_id": user_id_str,
        "source_image_id": image_id,
        "status": AnalysisStatus.COMPLETED,  # Screening tool analyses are already completed
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "parameters": params_dict,
        "results": {
            "timestamp": datetime.utcnow(),
            "analysis_subtype": analysis_subtype,
            "notes": notes
        }
    }
    
    result = analyses_col.insert_one(analysis_doc)
    analysis_id = str(result.inserted_id)
    
    # Handle optional result image upload
    if result_image:
        try:
            # Read file content
            content = await result_image.read()
            
            # Get output directory for this analysis
            output_dir = get_analysis_output_path(user_id_str, analysis_id, "screening_tool")
            
            # Generate filename
            file_ext = Path(result_image.filename).suffix.lower() or ".png"
            result_filename = f"result_{analysis_subtype}{file_ext}"
            result_path = output_dir / result_filename
            
            # Save file
            with open(result_path, "wb") as f:
                f.write(content)
            
            # Update analysis with result file path
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "results.result_image": str(result_path),
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            analysis_doc["results"]["result_image"] = str(result_path)
        except Exception as e:
            # Log error but don't fail the request - the analysis record is still valid
            import logging
            logging.error(f"Failed to save result image for screening tool analysis {analysis_id}: {e}")
    
    # Update Image document with analysis_id
    images_col = get_images_collection()
    images_col.update_one(
        {"_id": ObjectId(image_id)},
        {"$addToSet": {"analysis_ids": analysis_id}}
    )
    
    # Return the created analysis
    analysis_doc["_id"] = analysis_id
    return analysis_doc
