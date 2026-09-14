"""
Provenance Analysis Routes

Provides endpoints for triggering provenance analysis.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime
from bson import ObjectId

from typing import Optional, List

from app.utils.security import get_current_user
from app.db.mongodb import get_images_collection, get_analyses_collection
from app.schemas import (
    AnalysisStatus,
    JobType,
)
from app.services.job_logger import create_job_log
from app.utils.docker_provenance import check_provenance_health
from app.tasks.provenance import provenance_analysis_task
from pydantic import BaseModel, Field

router = APIRouter(
    prefix="/provenance",
    tags=["Provenance Analysis"]
)


class ProvenanceRequest(BaseModel):
    """Request to start provenance analysis"""
    image_id: str = Field(..., description="Query image ID")
    search_image_ids: Optional[List[str]] = Field(None, description="Optional list of image IDs to include in analysis. If empty/None, all user images are used.")
    k: int = Field(10, ge=1, le=100, description="Top-K candidates from CBIR")
    q: int = Field(5, ge=1, le=50, description="Top-Q candidates for expansion")
    max_depth: int = Field(3, ge=1, le=5, description="Maximum expansion depth")
    descriptor_type: str = Field("cv_rsift", description="Descriptor type (cv_sift, cv_rsift, vlfeat_sift_heq)")


class ProvenanceStatusResponse(BaseModel):
    """Provenance service status"""
    service: str = "provenance"
    healthy: bool
    message: str


@router.get("/health", response_model=ProvenanceStatusResponse)
def provenance_health():
    """
    Check Provenance service health status.
    """
    healthy, message = check_provenance_health()
    return ProvenanceStatusResponse(healthy=healthy, message=message)


@router.post("/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze_provenance(
    request: ProvenanceRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Start provenance analysis for a query image.
    
    This process:
    1. Finds similar images using CBIR
    2. Matches keypoints to verify content sharing
    3. Builds a graph of shared content
    """
    user_id = str(current_user["_id"])
    
    # Verify query image
    images_col = get_images_collection()
    query_image = images_col.find_one({
        "_id": ObjectId(request.image_id),
        "user_id": user_id
    })
    
    if not query_image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Query image not found or access denied"
        )
    
    # Create analysis record
    analyses_col = get_analyses_collection()
    analysis_doc = {
        "type": "provenance",  # Using string literal or add to AnalysisType enum
        "user_id": user_id,
        "source_image_id": request.image_id,
        "status": AnalysisStatus.PENDING,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "parameters": {
            "k": request.k,
            "q": request.q,
            "max_depth": request.max_depth,
            "descriptor_type": request.descriptor_type,
            "search_image_ids": request.search_image_ids
        }
    }
    result = analyses_col.insert_one(analysis_doc)
    analysis_id = str(result.inserted_id)
    
    # Update image with analysis reference
    images_col.update_one(
        {"_id": ObjectId(request.image_id)},
        {"$addToSet": {"analysis_ids": analysis_id}}
    )
    
    # Create job log entry for the jobs dashboard (pending state)
    job_id = create_job_log(
        user_id=user_id,
        job_type=JobType.PROVENANCE,
        title="Provenance Analysis",
        input_data={
            "image_id": request.image_id,
            "analysis_id": analysis_id,
            "k": request.k,
            "max_depth": request.max_depth
        }
    )
    
    # Trigger async task
    provenance_analysis_task.delay(
        analysis_id=analysis_id,
        user_id=user_id,
        query_image_id=request.image_id,
        search_image_ids=request.search_image_ids,
        k=request.k,
        q=request.q,
        max_depth=request.max_depth,
        descriptor_type=request.descriptor_type,
        job_id=job_id
    )
    
    return {
        "message": "Provenance analysis started",
        "analysis_id": analysis_id,
        "query_image_id": request.image_id
    }
