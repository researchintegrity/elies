"""
Pydantic schemas for request/response validation
"""
from pydantic import BaseModel, EmailStr, Field, field_validator
from datetime import datetime
from typing import Optional, Dict, List, Any, Literal
from bson import ObjectId
from enum import Enum
from app.config.settings import (
    USERNAME_MIN_LENGTH,
    USERNAME_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    FULL_NAME_MAX_LENGTH,
)


class UserLogin(BaseModel):
    """User login credentials"""
    username: str = Field(..., min_length=USERNAME_MIN_LENGTH, description="Username or email")
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, description="User password")

    class Config:
        schema_extra = {
            "example": {
                "username": "johndoe",
                "password": "securepassword123"
            }
        }


class UserRegister(BaseModel):
    """User registration data"""
    username: str = Field(..., min_length=USERNAME_MIN_LENGTH, max_length=USERNAME_MAX_LENGTH, description="Unique username")
    email: EmailStr = Field(..., description="Valid email address")
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, description=f"Password (min {PASSWORD_MIN_LENGTH} characters)")
    full_name: Optional[str] = Field(None, description="User's full name")

    class Config:
        schema_extra = {
            "example": {
                "username": "johndoe",
                "email": "john@example.com",
                "password": "securepassword123",
                "full_name": "John Doe"
            }
        }


class UserUpdate(BaseModel):
    """User profile update data"""
    full_name: Optional[str] = Field(None, max_length=FULL_NAME_MAX_LENGTH, description="User's full name")
    email: Optional[EmailStr] = Field(None, description="Valid email address")

    class Config:
        schema_extra = {
            "example": {
                "full_name": "John Doe Updated",
                "email": "newemail@example.com"
            }
        }


class UserResponse(BaseModel):
    """User response model"""
    id: str = Field(alias="_id")
    username: str
    email: str
    full_name: Optional[str] = None
    is_active: bool
    roles: List[str] = Field(default_factory=lambda: ["user"])
    storage_used_bytes: int = 0
    storage_limit_bytes: int = 1073741824  # 1 GB default
    created_at: datetime
    updated_at: datetime
    last_login_at: Optional[datetime] = None

    @field_validator('id', mode='before')
    @classmethod
    def convert_object_id(cls, v):
        """Convert MongoDB ObjectId to string"""
        if isinstance(v, ObjectId):
            return str(v)
        return v

    class Config:
        from_attributes = True
        schema_extra = {
            "example": {
                "_id": "507f1f77bcf86cd799439011",
                "username": "johndoe",
                "email": "john@example.com",
                "full_name": "John Doe",
                "is_active": True,
                "roles": ["user"],
                "storage_used_bytes": 524288000,
                "storage_limit_bytes": 1073741824,
                "created_at": "2025-01-01T10:00:00",
                "updated_at": "2025-01-02T15:30:00",
                "last_login_at": "2025-01-02T15:30:00"
            }
        }


class TokenResponse(BaseModel):
    """Authentication token response"""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    expires_in: int

    class Config:
        schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "user": {
                    "_id": "507f1f77bcf86cd799439011",
                    "username": "johndoe",
                    "email": "john@example.com",
                    "full_name": "John Doe",
                    "is_active": True,
                    "created_at": "2025-01-01T10:00:00",
                    "updated_at": "2025-01-02T15:30:00"
                },
                "expires_in": 86400
            }
        }


class MessageResponse(BaseModel):
    """Generic message response"""
    message: str

    class Config:
        schema_extra = {
            "example": {
                "message": "Operation completed successfully"
            }
        }


class ErrorResponse(BaseModel):
    """Error response model"""
    error: str
    status_code: int

    class Config:
        schema_extra = {
            "example": {
                "error": "Username or email already registered",
                "status_code": 400
            }
        }


class UserInDB(BaseModel):
    """User model stored in database"""
    username: str
    email: str
    hashed_password: str
    full_name: Optional[str] = None
    is_active: bool = True
    roles: List[str] = Field(default_factory=lambda: ["user"])
    storage_used_bytes: int = 0  # Total storage used (PDFs + images)
    storage_limit_bytes: int = 1073741824  # 1 GB default
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: Optional[datetime] = None


# ============================================================================
# Document Upload Schemas
# ============================================================================

class DocumentCreate(BaseModel):
    """Document creation request (internal use)"""
    user_id: str
    filename: str
    file_path: str
    file_size: int

    class Config:
        schema_extra = {
            "example": {
                "user_id": "507f1f77bcf86cd799439011",
                "filename": "research_paper.pdf",
                "file_path": "/workspace/507f1f77bcf86cd799439011/pdfs/1730000000_research_paper.pdf",
                "file_size": 2048576
            }
        }


class ExtractedImageInfo(BaseModel):
    """Information about an extracted image"""
    filename: str
    path: str
    size: int
    mime_type: str


class DocumentResponse(BaseModel):
    """Document response model"""
    id: str = Field(alias="_id")
    user_id: str
    filename: str
    file_path: str
    file_size: int
    extraction_status: str = Field(default="pending", description="pending|completed|failed")
    extracted_image_count: int = 0
    extraction_errors: list[str] = Field(default_factory=list)
    extracted_images: list[ExtractedImageInfo] = Field(default_factory=list)
    uploaded_date: datetime
    user_storage_used: int = 0  # Total bytes used by user
    user_storage_remaining: int = 1073741824  # Remaining quota

    @field_validator('id', mode='before')
    @classmethod
    def convert_object_id(cls, v):
        """Convert MongoDB ObjectId to string"""
        if isinstance(v, ObjectId):
            return str(v)
        return v

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "_id": "507f1f77bcf86cd799439012",
                "user_id": "507f1f77bcf86cd799439011",
                "filename": "research_paper.pdf",
                "file_path": "/workspace/507f1f77bcf86cd799439011/pdfs/1730000000_research_paper.pdf",
                "file_size": 2048576,
                "extraction_status": "completed",
                "extracted_image_count": 5,
                "extraction_errors": [],
                "uploaded_date": "2025-01-01T10:00:00",
                "user_storage_used": 524288000,
                "user_storage_remaining": 549453824
            }
        }


class DocumentInDB(BaseModel):
    """Document model stored in database"""
    user_id: str
    filename: str
    file_path: str
    file_size: int
    extraction_status: str = "pending"
    extracted_image_count: int = 0
    extraction_errors: list[str] = Field(default_factory=list)
    uploaded_date: datetime = Field(default_factory=datetime.utcnow)


class PaginatedDocumentResponse(BaseModel):
    """Paginated response for document listing - supports efficient pagination"""
    items: List["DocumentResponse"] = Field(description="List of documents for current page")
    total: int = Field(description="Total number of documents matching the query")
    page: int = Field(description="Current page number (1-indexed)")
    per_page: int = Field(description="Number of items per page")
    total_pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether there is a next page")
    has_prev: bool = Field(description="Whether there is a previous page")

    class Config:
        json_schema_extra = {
            "example": {
                "items": [],
                "total": 50,
                "page": 1,
                "per_page": 12,
                "total_pages": 5,
                "has_next": True,
                "has_prev": False
            }
        }


# ============================================================================
# Image Upload Schemas
# ============================================================================

class ImageCreate(BaseModel):
    """Image creation request (internal use)"""
    user_id: str
    filename: str
    file_path: str
    file_size: int
    source_type: str = Field(default="uploaded", description="extracted|uploaded")
    document_id: Optional[str] = Field(None, description="Reference to document if extracted")
    exif_metadata: Optional[Dict[str, Any]] = Field(None, description="EXIF metadata extracted from image")

    class Config:
        schema_extra = {
            "example": {
                "user_id": "507f1f77bcf86cd799439011",
                "filename": "figure_1.png",
                "file_path": "/workspace/507f1f77bcf86cd799439011/images/extracted/507f1f77bcf86cd799439012/figure_1.png",
                "file_size": 512000,
                "source_type": "extracted",
                "document_id": "507f1f77bcf86cd799439012",
                "exif_metadata": {"Make": "Canon", "Model": "Canon EOS 5D Mark IV"}
            }
        }


class CopyMoveAnalysisRequest(BaseModel):
    """Request to start copy-move analysis"""
    method: int = Field(2, ge=1, le=5, description="Detection method (1-5)")

    class Config:
        schema_extra = {
            "example": {
                "method": 2
            }
        }


class ImageResponse(BaseModel):
    """Image response model"""
    id: str = Field(alias="_id")
    user_id: str
    filename: str
    file_path: str
    file_size: int
    source_type: str = Field(description="extracted|uploaded|panel")
    document_id: Optional[str] = None
    # Panel extraction fields (only for source_type='panel')
    source_image_id: Optional[str] = None
    panel_id: Optional[str] = None
    panel_type: Optional[str] = None
    bbox: Optional[Dict[str, float]] = None  # {x0, y0, x1, y1}
    exif_metadata: Optional[Dict[str, Any]] = None
    # PDF extraction metadata (only for source_type='extracted' from PDFs)
    pdf_page: Optional[int] = None
    page_bbox: Optional[Dict[str, float]] = None  # {x0, y0, x1, y1} in pixel coordinates
    extraction_mode: Optional[str] = None  # 'normal' or 'safe'
    original_filename: Optional[str] = None  # Original filename before _id rename
    # Image type management
    image_type: List[str] = Field(default_factory=list, description="User-editable image types (e.g., 'figure', 'table', 'equation')")
    
    # Analysis fields
    analysis_status: Dict[str, str] = Field(default_factory=dict, description="Status of various analyses (e.g. {'copy_move': 'processing'})")
    analysis_results: Dict[str, Dict] = Field(default_factory=dict, description="Results of analyses")
    analysis_ids: List[str] = Field(default_factory=list, description="List of analysis IDs involving this image")
    
    # Flagged for review
    is_flagged: bool = Field(default=False, description="Whether image is flagged as suspicious for review")
    
    uploaded_date: datetime
    user_storage_used: int = 0  # Total bytes used by user
    user_storage_remaining: int = 1073741824  # Remaining quota

    @field_validator('id', mode='before')
    @classmethod
    def convert_object_id(cls, v):
        """Convert MongoDB ObjectId to string"""
        if isinstance(v, ObjectId):
            return str(v)
        return v

    class Config:
        from_attributes = True
        schema_extra = {
            "example": {
                "_id": "507f1f77bcf86cd799439013",
                "user_id": "507f1f77bcf86cd799439011",
                "filename": "507f1f77bcf86cd799439013.png",
                "file_path": "workspace/507f1f77bcf86cd799439011/images/extracted/507f1f77bcf86cd799439012/507f1f77bcf86cd799439013.png",
                "file_size": 512000,
                "source_type": "extracted",
                "document_id": "507f1f77bcf86cd799439012",
                "source_image_id": None,
                "panel_id": None,
                "panel_type": None,
                "bbox": None,
                "pdf_page": 4,
                "page_bbox": {"x0": 40.0, "y0": 59.28, "x1": 553.6, "y1": 492.0},
                "extraction_mode": "normal",
                "original_filename": "p-4-x0-40.000-y0-59.280-x1-553.600-y1-492.000-1.png",
                "image_type": ["figure"],
                "uploaded_date": "2025-01-01T10:00:00",
                "user_storage_used": 524288000,
                "user_storage_remaining": 549453824
            }
        }


class ImageInDB(BaseModel):
    """Image model stored in database"""
    user_id: str
    filename: str
    file_path: str
    file_size: int
    source_type: str = "uploaded"
    document_id: Optional[str] = None
    uploaded_date: datetime = Field(default_factory=datetime.utcnow)
    analysis_ids: List[str] = Field(default_factory=list)
    is_flagged: bool = Field(default=False, description="Whether image is flagged as suspicious")


class PaginatedImageResponse(BaseModel):
    """Paginated response for image listing - supports efficient gallery pagination"""
    items: List["ImageResponse"] = Field(description="List of images for current page")
    total: int = Field(description="Total number of images matching the query")
    page: int = Field(description="Current page number (1-indexed)")
    per_page: int = Field(description="Number of items per page")
    total_pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether there is a next page")
    has_prev: bool = Field(description="Whether there is a previous page")
    
    class Config:
        schema_extra = {
            "example": {
                "items": [],
                "total": 150,
                "page": 1,
                "per_page": 24,
                "total_pages": 7,
                "has_next": True,
                "has_prev": False
            }
        }


# ============================================================================
# IMAGE TYPE MANAGEMENT MODELS
# ============================================================================

class ImageTypeListResponse(BaseModel):
    """Response for listing all image types in the system"""
    types: List[str] = Field(description="List of all unique image types used in system")
    count: int = Field(description="Total number of unique types")

    class Config:
        schema_extra = {
            "example": {
                "types": ["figure", "table", "equation", "text"],
                "count": 4
            }
        }


class ImageTypesUpdateRequest(BaseModel):
    """Request to add types to an image"""
    types: List[str] = Field(..., description="List of types to add (duplicates ignored)")

    class Config:
        schema_extra = {
            "example": {
                "types": ["figure", "graph"]
            }
        }


# ============================================================================
# API RESPONSE MODELS
# ============================================================================

class ApiResponse(BaseModel):
    """Standardized API response wrapper"""
    success: bool = Field(..., description="Whether the request was successful")
    message: str = Field(..., description="Response message")
    data: Optional[dict] = Field(None, description="Response data payload")
    errors: Optional[list] = Field(None, description="List of errors if any")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Response timestamp")

    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "message": "Operation completed successfully",
                "data": {"id": "507f1f77bcf86cd799439011"},
                "errors": None,
                "timestamp": "2025-01-01T10:00:00"
            }
        }


class PaginatedResponse(BaseModel):
    """Paginated API response with metadata"""
    success: bool = Field(..., description="Whether the request was successful")
    message: str = Field(..., description="Response message")
    data: list = Field(..., description="List of items")
    pagination: dict = Field(..., description="Pagination metadata")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Response timestamp")

    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "message": "Items retrieved successfully",
                "data": [{"id": "507f1f77bcf86cd799439011"}],
                "pagination": {
                    "current_page": 1,
                    "total_pages": 10,
                    "page_size": 10,
                    "total_items": 100
                },
                "timestamp": "2025-01-01T10:00:00"
            }
        }


# ============================================================================
# Annotation Schemas
# ============================================================================

class PolygonPoint(BaseModel):
    """A single point in a polygon"""
    x: float = Field(..., description="X coordinate (percentage)")
    y: float = Field(..., description="Y coordinate (percentage)")


class CoordinateInfo(BaseModel):
    """Annotation coordinates - supports rectangles, ellipses, and polygons"""
    x: float = Field(0, description="X coordinate (percentage)")
    y: float = Field(0, description="Y coordinate (percentage)")
    width: float = Field(0, description="Width (percentage)")
    height: float = Field(0, description="Height (percentage)")
    points: Optional[List[PolygonPoint]] = Field(None, description="Polygon points (for polygon shapes)")

    class Config:
        schema_extra = {
            "example": {
                "x": 25.5,
                "y": 30.1,
                "width": 10.2,
                "height": 15.8
            }
        }


# Legacy Annotation Schemas removed per user request



# ============================================================================
# Single Annotation Schemas (for single-image annotations)
# ============================================================================

class SingleAnnotationCreate(BaseModel):
    """Single-image annotation creation request"""
    image_id: str = Field(..., description="ID of the image being annotated")
    text: str = Field("", max_length=1000, description="Annotation text/description")
    coords: CoordinateInfo = Field(..., description="Annotation coordinates")
    type: Optional[str] = Field("manipulation", description="Annotation type/label")
    shape_type: Optional[Literal["rectangle", "ellipse", "polygon"]] = Field("rectangle", description="Shape type")

    class Config:
        json_schema_extra = {
            "example": {
                "image_id": "507f1f77bcf86cd799439013",
                "text": "Detected manipulation region",
                "coords": {"x": 25.5, "y": 30.1, "width": 10.2, "height": 15.8},
                "type": "manipulation",
                "shape_type": "rectangle"
            }
        }


class SingleAnnotationResponse(BaseModel):
    """Single-image annotation response model"""
    id: str = Field(alias="_id", serialization_alias="_id")
    user_id: str
    image_id: str
    text: str
    coords: CoordinateInfo
    type: Optional[str] = "manipulation"
    shape_type: Optional[str] = "rectangle"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


# ============================================================================
# Dual Annotation Schemas (for cross-image annotations)
# ============================================================================

class DualAnnotationCreate(BaseModel):
    """Dual-image annotation creation request - annotation box on one image linked to another"""
    source_image_id: str = Field(..., description="Image where this annotation is drawn")
    target_image_id: str = Field(..., description="The linked target image")
    link_id: str = Field(..., description="Unique ID linking source and target annotations")
    coords: CoordinateInfo = Field(..., description="Annotation coordinates")
    pair_name: Optional[str] = Field(None, description="User-defined pair name (e.g., 'Pair 1')")
    pair_color: Optional[str] = Field(None, description="Pair color hex code")
    text: str = Field("", max_length=1000, description="Annotation text/description")
    shape_type: Optional[Literal["rectangle", "ellipse", "polygon"]] = Field("rectangle", description="Shape type")

    class Config:
        json_schema_extra = {
            "example": {
                "source_image_id": "507f1f77bcf86cd799439013",
                "target_image_id": "507f1f77bcf86cd799439014",
                "link_id": "link_abc123",
                "coords": {"x": 25.5, "y": 30.1, "width": 10.2, "height": 15.8},
                "pair_name": "Pair 1",
                "pair_color": "#EF4444",
                "text": "Matched region",
                "shape_type": "rectangle"
            }
        }


class DualAnnotationResponse(BaseModel):
    """Dual-image annotation response model"""
    id: str = Field(alias="_id", serialization_alias="_id")
    user_id: str
    source_image_id: str
    target_image_id: str
    link_id: str
    coords: CoordinateInfo
    pair_name: Optional[str] = None
    pair_color: Optional[str] = None
    text: str
    shape_type: Optional[str] = "rectangle"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class DualAnnotationBatchCreate(BaseModel):
    """Batch create dual annotations"""
    annotations: List[DualAnnotationCreate] = Field(..., min_length=1, max_length=100, description="List of dual annotations to create")

    class Config:
        json_schema_extra = {
            "example": {
                "annotations": [
                    {
                        "source_image_id": "507f1f77bcf86cd799439013",
                        "target_image_id": "507f1f77bcf86cd799439014",
                        "link_id": "link_abc123",
                        "coords": {"x": 25.5, "y": 30.1, "width": 10.2, "height": 15.8},
                        "pair_name": "Pair 1",
                        "pair_color": "#EF4444"
                    }
                ]
            }
        }


class DualAnnotationUpdate(BaseModel):
    """Dual-image annotation update request - partial update"""
    coords: Optional[CoordinateInfo] = Field(None, description="Updated annotation coordinates")
    pair_name: Optional[str] = Field(None, description="Updated pair name")
    pair_color: Optional[str] = Field(None, description="Updated pair color hex code")
    text: Optional[str] = Field(None, max_length=1000, description="Updated annotation text")

    class Config:
        json_schema_extra = {
            "example": {
                "coords": {"x": 30.0, "y": 35.0, "width": 12.0, "height": 18.0},
                "pair_name": "Renamed Pair",
                "pair_color": "#3B82F6"
            }
        }


# ============================================================================
# Watermark Removal Schemas
# ============================================================================

class WatermarkRemovalRequest(BaseModel):
    """Watermark removal request"""
    aggressiveness_mode: int = Field(
        default=2,
        ge=1,
        le=3,
        description="Watermark removal aggressiveness (1=explicit only, 2=text+graphics, 3=all graphics)"
    )

    class Config:
        schema_extra = {
            "example": {
                "aggressiveness_mode": 2
            }
        }


class WatermarkRemovalInitiationResponse(BaseModel):
    """Watermark removal task initiation response"""
    document_id: str
    task_id: str
    status: str = Field(description="Task status: queued")
    aggressiveness_mode: int
    message: str

    class Config:
        schema_extra = {
            "example": {
                "document_id": "507f1f77bcf86cd799439012",
                "task_id": "abc123def456",
                "status": "queued",
                "aggressiveness_mode": 2,
                "message": "Watermark removal queued with mode 2"
            }
        }


class WatermarkRemovalStatusResponse(BaseModel):
    """Watermark removal status response"""
    document_id: str
    status: str = Field(description="Status: not_started|queued|processing|completed|failed")
    aggressiveness_mode: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    message: Optional[str] = None
    output_filename: Optional[str] = None
    output_size: Optional[int] = None
    cleaned_document_id: Optional[str] = None
    error: Optional[str] = None

    class Config:
        schema_extra = {
            "example": {
                "document_id": "507f1f77bcf86cd799439012",
                "status": "completed",
                "aggressiveness_mode": 2,
                "started_at": "2025-01-01T10:00:00",
                "completed_at": "2025-01-01T10:05:00",
                "message": "Watermark removal successful",
                "output_filename": "research_paper_watermark_removed_m2.pdf",
                "output_size": 1990000,
                "cleaned_document_id": "507f1f77bcf86cd799439015",
                "error": None
            }
        }


# ============================================================================
# Panel Extraction Schemas
# ============================================================================

class PanelExtractionRequest(BaseModel):
    """Panel extraction request"""
    image_ids: list[str] = Field(..., description="MongoDB IDs of selected images to extract panels from")
    model_type: str = Field(
        default="default",
        description="Optional YOLO model selection"
    )

    class Config:
        schema_extra = {
            "example": {
                "image_ids": ["507f1f77bcf86cd799439013", "507f1f77bcf86cd799439014"],
                "model_type": "default"
            }
        }


class PanelExtractionInitiationResponse(BaseModel):
    """Panel extraction task initiation response"""
    task_id: str
    status: str = Field(description="Task status: queued")
    image_ids: list[str]
    message: str

    class Config:
        schema_extra = {
            "example": {
                "task_id": "abc123def456xyz",
                "status": "queued",
                "image_ids": ["507f1f77bcf86cd799439013", "507f1f77bcf86cd799439014"],
                "message": "Panel extraction queued for 2 images"
            }
        }


class PanelExtractionStatusResponse(BaseModel):
    """Panel extraction status response"""
    task_id: str
    status: str = Field(description="Status: queued|processing|completed|failed")
    image_ids: list[str]
    extracted_panels_count: int = 0
    extracted_panels: Optional[list[ImageResponse]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    message: Optional[str] = None
    error: Optional[str] = None

    class Config:
        schema_extra = {
            "example": {
                "task_id": "abc123def456xyz",
                "status": "completed",
                "image_ids": ["507f1f77bcf86cd799439013"],
                "extracted_panels_count": 3,
                "extracted_panels": [
                    {
                        "_id": "507f1f77bcf86cd799439020",
                        "user_id": "507f1f77bcf86cd799439011",
                        "filename": "panel_00001.png",
                        "file_path": "/workspace/507f1f77bcf86cd799439011/images/panels/panel_00001.png",
                        "file_size": 45000,
                        "source_type": "panel",
                        "document_id": None,
                        "source_image_id": "507f1f77bcf86cd799439013",
                        "panel_id": "panel_00001",
                        "panel_type": "Blots",
                        "bbox": {"x0": 100.5, "y0": 150.3, "x1": 450.8, "y1": 520.2},
                        "uploaded_date": "2025-01-01T10:05:00",
                        "user_storage_used": 524288000,
                        "user_storage_remaining": 549453824
                    }
                ],
                "started_at": "2025-01-01T10:00:00",
                "completed_at": "2025-01-01T10:02:30",
                "message": "Panel extraction successful. Extracted 3 panels",
                "error": None
            }
        }


# ============================================================================
# CBIR (Content-Based Image Retrieval) SCHEMAS
# ============================================================================

class CBIRIndexRequest(BaseModel):
    """Request to index images in CBIR system"""
    image_ids: Optional[List[str]] = Field(None, description="Specific image IDs to index. If None, indexes all user images.")
    labels: Optional[List[str]] = Field(None, description="Labels to apply to indexed images")

    class Config:
        json_schema_extra = {
            "example": {
                "image_ids": ["507f1f77bcf86cd799439013", "507f1f77bcf86cd799439014"],
                "labels": ["Western Blot", "Microscopy"]
            }
        }


class CBIRSearchRequest(BaseModel):
    """Request to search for similar images"""
    image_id: str = Field(..., description="Query image ID")
    top_k: int = Field(10, ge=1, le=100, description="Number of similar images to return")
    labels: Optional[List[str]] = Field(None, description="Filter results by labels")

    class Config:
        json_schema_extra = {
            "example": {
                "image_id": "507f1f77bcf86cd799439013",
                "top_k": 10,
                "labels": ["Western Blot"]
            }
        }


class CBIRSearchResult(BaseModel):
    """Single CBIR search result"""
    cbir_id: Optional[int] = Field(None, description="CBIR internal ID")
    distance: float = Field(..., description="Distance/dissimilarity score (lower is more similar)")
    similarity_score: float = Field(..., description="Similarity score (higher is more similar)")
    image_path: str = Field(..., description="Path to the similar image")
    image_id: Optional[str] = Field(None, description="MongoDB image ID")
    filename: Optional[str] = Field(None, description="Image filename")
    file_size: Optional[int] = Field(None, description="Image file size in bytes")
    source_type: Optional[str] = Field(None, description="Image source type")
    document_id: Optional[str] = Field(None, description="Source document ID if extracted")
    image_type: List[str] = Field(default_factory=list, description="Image type labels")
    cbir_labels: List[str] = Field(default_factory=list, description="CBIR index labels")
    is_flagged: bool = Field(default=False, description="Whether image is flagged as suspicious")


class CBIRSearchResponse(BaseModel):
    """CBIR search response with results"""
    query_image_id: str
    top_k: int
    labels_filter: Optional[List[str]] = None
    matches_count: int
    matches: List[CBIRSearchResult]


class CBIRDeleteRequest(BaseModel):
    """Request to delete images from CBIR index"""
    image_ids: List[str] = Field(..., min_length=1, description="Image IDs to remove from index")

    class Config:
        json_schema_extra = {
            "example": {
                "image_ids": ["507f1f77bcf86cd799439013", "507f1f77bcf86cd799439014"]
            }
        }


class CBIRStatusResponse(BaseModel):
    """CBIR service status"""
    service: str = "cbir"
    healthy: bool
    message: str
    timestamp: datetime


# ============================================================================
# INDEXING JOB SCHEMAS (Batch Image Indexing Progress Tracking)
# ============================================================================

class IndexingJobStatus(str, Enum):
    """Status of a batch indexing job"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # Some images failed, others succeeded


class IndexingJobResponse(BaseModel):
    """Response model for indexing job status"""
    job_id: str = Field(..., description="Unique job identifier")
    user_id: str = Field(..., description="User who initiated the job")
    status: IndexingJobStatus = Field(..., description="Current job status")
    total_images: int = Field(..., description="Total images to index")
    processed_images: int = Field(0, description="Images processed so far")
    indexed_images: int = Field(0, description="Images successfully indexed")
    failed_images: int = Field(0, description="Images that failed to index")
    progress_percent: float = Field(0.0, description="Progress percentage (0-100)")
    current_step: str = Field("", description="Current processing step description")
    errors: List[str] = Field(default_factory=list, description="List of error messages")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")

    class Config:
        json_schema_extra = {
            "example": {
                "job_id": "idx_507f1f77bcf86cd799439011_1704393600",
                "user_id": "507f1f77bcf86cd799439011",
                "status": "processing",
                "total_images": 10,
                "processed_images": 5,
                "indexed_images": 5,
                "failed_images": 0,
                "progress_percent": 50.0,
                "current_step": "Encoding image 5 of 10",
                "errors": [],
                "created_at": "2025-01-04T18:00:00Z",
                "updated_at": "2025-01-04T18:01:30Z",
                "completed_at": None
            }
        }


class BatchUploadResponse(BaseModel):
    """Response for batch image upload"""
    job_id: str = Field(..., description="Indexing job ID for tracking progress")
    uploaded_count: int = Field(..., description="Number of images uploaded")
    image_ids: List[str] = Field(..., description="MongoDB IDs of uploaded images")
    message: str = Field(..., description="Status message")

    class Config:
        json_schema_extra = {
            "example": {
                "job_id": "idx_507f1f77bcf86cd799439011_1704393600",
                "uploaded_count": 5,
                "image_ids": ["507f1f77bcf86cd799439013", "507f1f77bcf86cd799439014"],
                "message": "5 images uploaded, indexing in progress"
            }
        }


# ============================================================================
# JOB MONITORING SCHEMAS (Unified Background Job Tracking)
# ============================================================================

class JobType(str, Enum):
    """Types of background jobs tracked in the jobs dashboard"""
    CBIR_INDEX = "cbir_index"
    CBIR_SEARCH = "cbir_search"
    CBIR_DELETE = "cbir_delete"
    COPY_MOVE_SINGLE = "copy_move_single"
    COPY_MOVE_CROSS = "copy_move_cross"
    TRUFOR = "trufor"
    PANEL_EXTRACTION = "panel_extraction"
    IMAGE_EXTRACTION = "image_extraction"
    WATERMARK_REMOVAL = "watermark_removal"
    PROVENANCE = "provenance"
    IMAGE_DELETION = "image_deletion"
    DOCUMENT_DELETION = "document_deletion"
    BATCH_UPLOAD = "batch_upload"


class JobStatus(str, Enum):
    """Status of a background job"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # Some items succeeded, others failed


class JobLogResponse(BaseModel):
    """Response model for a job log entry"""
    job_id: str = Field(..., description="Unique job identifier")
    user_id: str = Field(..., description="User who initiated the job")
    job_type: JobType = Field(..., description="Type of background job")
    celery_task_id: Optional[str] = Field(None, description="Celery task ID if applicable")
    status: JobStatus = Field(..., description="Current job status")
    title: str = Field(..., description="Human-readable job title")
    progress_percent: float = Field(0.0, description="Progress percentage (0-100)")
    current_step: str = Field("", description="Current processing step description")
    input_data: Optional[Dict[str, Any]] = Field(None, description="Job input parameters")
    output_data: Optional[Dict[str, Any]] = Field(None, description="Job results summary")
    errors: List[str] = Field(default_factory=list, description="Error messages if any")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = Field(None, description="When processing started")
    completed_at: Optional[datetime] = Field(None, description="When job completed")
    expires_at: Optional[datetime] = Field(None, description="When job log will be deleted")

    class Config:
        json_schema_extra = {
            "example": {
                "job_id": "job_507f1f77bcf86cd799439011_1704393600_abc12345",
                "user_id": "507f1f77bcf86cd799439011",
                "job_type": "trufor",
                "celery_task_id": "abc123-def456-ghi789",
                "status": "completed",
                "title": "TruFor Analysis on image.jpg",
                "progress_percent": 100.0,
                "current_step": "Completed",
                "created_at": "2025-01-04T18:00:00Z",
                "updated_at": "2025-01-04T18:01:30Z",
                "completed_at": "2025-01-04T18:01:30Z",
                "expires_at": "2025-01-11T18:01:30Z"
            }
        }


class JobListResponse(BaseModel):
    """Paginated list of jobs for dashboard table"""
    items: List[JobLogResponse]
    total: int = Field(..., description="Total number of jobs matching filters")
    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Items per page")
    total_pages: int = Field(..., description="Total number of pages")
    has_next: bool = Field(..., description="Whether there is a next page")
    has_prev: bool = Field(..., description="Whether there is a previous page")


class JobStatsResponse(BaseModel):
    """Summary statistics for jobs dashboard header cards"""
    total_jobs: int = Field(..., description="Total jobs count")
    pending: int = Field(0, description="Jobs waiting to start")
    processing: int = Field(0, description="Jobs currently running")
    completed: int = Field(0, description="Successfully completed jobs")
    failed: int = Field(0, description="Failed jobs")
    by_type: Dict[str, int] = Field(default_factory=dict, description="Job counts by type")


class JobNotification(BaseModel):
    """SSE notification payload for real-time job status changes"""
    event: str = Field(..., description="Event type: job_started, job_progress, job_completed, job_failed")
    job_id: str = Field(..., description="Job identifier")
    job_type: str = Field(..., description="Type of job")
    status: str = Field(..., description="Current status")
    title: Optional[str] = Field(None, description="Job title for display")
    progress_percent: Optional[float] = Field(None, description="Progress if applicable")
    current_step: Optional[str] = Field(None, description="Current step if applicable")
    error: Optional[str] = Field(None, description="Error message if failed")


# ============================================================================
# ANALYSIS SCHEMAS
# ============================================================================

class CopyMoveMethod(str, Enum):
    """Detection method for copy-move analysis"""
    DENSE = "dense"  # Dense matching (original copy-move-detection module, methods 1-5)
    KEYPOINT = "keypoint"  # Keypoint matching (copy-move-detection-keypoint module, cross-image only)


class KeypointDescriptor(str, Enum):
    """Descriptor types for keypoint-based copy-move detection"""
    CV_SIFT = "cv_sift"  # OpenCV SIFT
    CV_RSIFT = "cv_rsift"  # OpenCV RootSIFT (default, recommended)
    VLFEAT_SIFT_HEQ = "vlfeat_sift_heq"  # VLFeat SIFT with histogram equalization


class AnalysisType(str, Enum):
    SINGLE_IMAGE_COPY_MOVE = "single_image_copy_move"
    CROSS_IMAGE_COPY_MOVE = "cross_image_copy_move"
    TRUFOR = "trufor"
    CBIR_SEARCH = "cbir_search"
    PROVENANCE = "provenance"
    SCREENING_TOOL = "screening_tool"  # Client-side screening tools (ELA, Noise Analysis, Magnifier, etc.)


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisBase(BaseModel):
    """Base analysis model"""
    type: AnalysisType
    user_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    status: AnalysisStatus = AnalysisStatus.PENDING
    error: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Analysis-specific parameters used to configure this analysis"
    )


class SingleImageAnalysisCreate(BaseModel):
    """Request to create a single image analysis (dense method only)"""
    image_id: str
    # Single-image detection only supports dense method
    method: CopyMoveMethod = Field(
        CopyMoveMethod.DENSE,
        description="Detection method for single image (only 'dense' supported)"
    )
    # Dense method sub-parameter (1-5)
    dense_method: int = Field(2, ge=1, le=5, description="Dense method variant (1-5)")


class TruForAnalysisCreate(BaseModel):
    """Request to create a TruFor forgery detection analysis"""
    image_id: str = Field(..., description="ID of the image to analyze")
    save_noiseprint: bool = Field(
        default=False,
        description="Whether to save the noiseprint map (useful for advanced analysis)"
    )


class ScreeningToolAnalysisCreate(BaseModel):
    """Request to save a screening tool/client-side analysis result"""
    image_id: str = Field(..., description="ID of the image that was analyzed")
    analysis_subtype: str = Field(
        ...,
        description="Subtype of analysis (e.g., 'ela', 'noise_analysis', 'magnifier', 'histogram')"
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters used in the client-side analysis (e.g., quality level for ELA)"
    )
    notes: Optional[str] = Field(
        None,
        max_length=2000,
        description="Optional notes or observations about the analysis"
    )


class CrossImageAnalysisCreate(BaseModel):
    """Request to create a cross image analysis"""
    source_image_id: str
    target_image_id: str
    method: CopyMoveMethod = Field(
        CopyMoveMethod.KEYPOINT,
        description="Detection method: 'keypoint' (recommended) or 'dense'"
    )
    # Dense method sub-parameter (only used when method='dense')
    dense_method: int = Field(2, ge=1, le=5, description="Dense method variant (1-5), only used when method='dense'")
    # Keypoint descriptor (only used when method='keypoint')
    descriptor: KeypointDescriptor = Field(
        KeypointDescriptor.CV_RSIFT,
        description="Keypoint descriptor type, only used when method='keypoint'"
    )


class AnalysisResult(BaseModel):
    """Generic analysis result container"""
    # Method can be string ('keypoint', 'dense') for copy-move or int for legacy
    method: Optional[Any] = None
    dense_method: Optional[int] = None  # Sub-method for dense detection (1-5)
    descriptor: Optional[str] = None  # Keypoint descriptor type (cv_sift, cv_rsift, vlfeat_sift_heq)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    matches_image: Optional[str] = None
    clusters_image: Optional[str] = None
    visualization: Optional[str] = None
    files: Optional[List[str]] = None
    logs: Optional[Dict[str, Any]] = None
    
    # TruFor specific fields
    pred_map: Optional[str] = None  # Prediction/localization map path
    conf_map: Optional[str] = None  # Confidence map path
    noiseprint: Optional[str] = None  # Noiseprint map path (optional)
    
    # CBIR specific fields
    query_image_id: Optional[str] = None
    top_k: Optional[int] = None
    labels_filter: Optional[List[str]] = None
    matches_count: Optional[int] = None
    matches: Optional[List[CBIRSearchResult]] = None

    class Config:
        extra = "allow"


class AnalysisResponse(AnalysisBase):
    """Analysis response model"""
    id: str = Field(alias="_id")
    source_image_id: str
    target_image_id: Optional[str] = None
    results: Optional[AnalysisResult] = None

    @field_validator('id', mode='before')
    @classmethod
    def convert_object_id(cls, v):
        """Convert MongoDB ObjectId to string"""
        if isinstance(v, ObjectId):
            return str(v)
        return v

    class Config:
        json_encoders = {ObjectId: str}
        populate_by_name = True


# ============================================================================
# Admin Panel Schemas
# ============================================================================

class AdminUserResponse(BaseModel):
    """Extended user response for admin panel"""
    id: str = Field(alias="_id")
    username: str
    email: str
    full_name: Optional[str] = None
    is_active: bool
    roles: List[str] = Field(default_factory=lambda: ["user"])
    storage_used_bytes: int = 0
    storage_limit_bytes: int = 1073741824
    created_at: datetime
    updated_at: datetime
    last_login_at: Optional[datetime] = None

    @field_validator('id', mode='before')
    @classmethod
    def convert_object_id(cls, v):
        """Convert MongoDB ObjectId to string"""
        if isinstance(v, ObjectId):
            return str(v)
        return v

    class Config:
        from_attributes = True
        populate_by_name = True


class AdminUserListResponse(BaseModel):
    """Paginated list of users for admin panel"""
    users: List[AdminUserResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminUpdateQuotaRequest(BaseModel):
    """Request to update a user's storage quota"""
    storage_limit_bytes: int = Field(
        ...,
        gt=0,
        description="New storage limit in bytes (must be positive)"
    )

    class Config:
        schema_extra = {
            "example": {
                "storage_limit_bytes": 5368709120  # 5 GB
            }
        }


class AdminUpdateRoleRequest(BaseModel):
    """Request to update a user's roles"""
    roles: List[str] = Field(
        ...,
        min_length=1,
        description="List of roles to assign to the user"
    )

    @field_validator('roles')
    @classmethod
    def validate_roles(cls, v):
        """Validate that roles are valid"""
        valid_roles = {"user", "admin"}
        for role in v:
            if role not in valid_roles:
                raise ValueError(f"Invalid role: {role}. Valid roles are: {valid_roles}")
        if "user" not in v:
            v = ["user"] + v  # Always include 'user' base role
        return list(set(v))  # Remove duplicates

    class Config:
        schema_extra = {
            "example": {
                "roles": ["user", "admin"]
            }
        }


class AdminResetPasswordRequest(BaseModel):
    """Request to reset a user's password (optional - if not provided, generates random)"""
    new_password: Optional[str] = Field(
        None,
        min_length=PASSWORD_MIN_LENGTH,
        description=f"New password (min {PASSWORD_MIN_LENGTH} characters). If not provided, a secure random password will be generated."
    )

    class Config:
        schema_extra = {
            "example": {
                "new_password": "NewSecurePassword123"
            }
        }


class AdminResetPasswordResponse(BaseModel):
    """Response after password reset"""
    message: str
    generated_password: Optional[str] = Field(
        None,
        description="Only returned if password was auto-generated"
    )


class AdminUpdateUserStatusRequest(BaseModel):
    """Request to activate/deactivate a user"""
    is_active: bool = Field(..., description="Set to true to activate, false to deactivate")

    class Config:
        schema_extra = {
            "example": {
                "is_active": False
            }
        }


# ============================================================================
# Image Relationship Schemas
# ============================================================================

class RelationshipSourceType(str, Enum):
    """Source of the relationship between images"""
    PROVENANCE = "provenance"
    CROSS_COPY_MOVE = "cross_copy_move"
    SIMILARITY = "similarity"
    MANUAL = "manual"


class ImageRelationshipCreate(BaseModel):
    """Request to create a relationship between two images"""
    image1_id: str = Field(..., description="First image ID")
    image2_id: str = Field(..., description="Second image ID")
    source_type: RelationshipSourceType = Field(
        default=RelationshipSourceType.MANUAL,
        description="Source of the relationship"
    )
    source_analysis_id: Optional[str] = Field(
        None,
        description="Reference to analysis that discovered this relationship"
    )
    weight: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Relationship strength (0-1, higher = stronger)"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional context (matched keypoints, shared area, etc.)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "image1_id": "507f1f77bcf86cd799439013",
                "image2_id": "507f1f77bcf86cd799439014",
                "source_type": "manual",
                "weight": 1.0
            }
        }


class ImageRelationshipResponse(BaseModel):
    """Response model for a relationship"""
    id: str = Field(alias="_id")
    user_id: str
    image1_id: str
    image2_id: str
    source_type: str
    source_analysis_id: Optional[str] = None
    weight: float
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    created_by: str = Field(description="'system' or user_id for manual")
    # Enriched field (populated on query)
    other_image: Optional[Dict[str, Any]] = Field(
        None,
        description="Basic info of the related image (filename, thumbnail, is_flagged)"
    )

    @field_validator('id', mode='before')
    @classmethod
    def convert_object_id(cls, v):
        """Convert MongoDB ObjectId to string"""
        if isinstance(v, ObjectId):
            return str(v)
        return v

    class Config:
        from_attributes = True
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "_id": "507f1f77bcf86cd799439015",
                "user_id": "507f1f77bcf86cd799439011",
                "image1_id": "507f1f77bcf86cd799439013",
                "image2_id": "507f1f77bcf86cd799439014",
                "source_type": "provenance",
                "weight": 0.85,
                "created_at": "2025-01-01T10:00:00",
                "created_by": "system"
            }
        }


class RelationshipGraphNode(BaseModel):
    """Node in the relationship graph"""
    id: str = Field(..., description="Image ID")
    label: str = Field(..., description="Image filename")
    is_flagged: bool = Field(default=False, description="Whether image is flagged")
    is_query: bool = Field(default=False, description="Whether this is the query image")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "507f1f77bcf86cd799439013",
                "label": "figure_1.png",
                "is_flagged": True,
                "is_query": True
            }
        }


class RelationshipGraphEdge(BaseModel):
    """Edge in the relationship graph"""
    source: str = Field(..., description="Source image ID")
    target: str = Field(..., description="Target image ID")
    weight: float = Field(..., description="Edge weight")
    source_type: str = Field(..., description="Relationship source type")
    id: str = Field(..., description="Relationship ID")
    is_mst_edge: bool = Field(
        default=False,
        description="Whether part of Maximum Spanning Tree (render darker)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "source": "507f1f77bcf86cd799439013",
                "target": "507f1f77bcf86cd799439014",
                "weight": 0.85,
                "source_type": "provenance",
                "is_mst_edge": True
            }
        }


class RelationshipGraphResponse(BaseModel):
    """Full graph structure for visualization"""
    query_image_id: str = Field(..., description="The image from which the graph was built")
    nodes: List[RelationshipGraphNode] = Field(
        default_factory=list,
        description="All nodes in the graph"
    )
    edges: List[RelationshipGraphEdge] = Field(
        default_factory=list,
        description="All edges in the graph"
    )
    mst_edges: List[RelationshipGraphEdge] = Field(
        default_factory=list,
        description="Maximum Spanning Tree edges (for darker rendering)"
    )
    total_nodes_count: int = Field(
        default=0,
        description="Total number of nodes in the full connected graph (unlimited depth)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "query_image_id": "507f1f77bcf86cd799439013",
                "nodes": [
                    {"id": "507f1f77bcf86cd799439013", "label": "fig1.png", "is_flagged": True, "is_query": True},
                    {"id": "507f1f77bcf86cd799439014", "label": "fig2.png", "is_flagged": True, "is_query": False}
                ],
                "edges": [
                    {"source": "507f1f77bcf86cd799439013", "target": "507f1f77bcf86cd799439014", 
                     "weight": 0.85, "source_type": "provenance", "is_mst_edge": True}
                ],
                "mst_edges": [
                    {"source": "507f1f77bcf86cd799439013", "target": "507f1f77bcf86cd799439014",
                     "weight": 0.85, "source_type": "provenance", "is_mst_edge": True}
                ]
            }
        }
