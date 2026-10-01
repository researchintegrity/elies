"""
Provenance Analysis API Client

This module provides functions to interact with the Provenance Analysis microservice.
"""
import logging
import requests

from app.exceptions import TransientError
from typing import Tuple, Dict, List, Any
from app.config.settings import (
    PROVENANCE_SERVICE_URL,
    PROVENANCE_TIMEOUT,
    convert_host_path_to_container,
)

logger = logging.getLogger(__name__)


def check_provenance_health() -> Tuple[bool, str]:
    """
    Check if the Provenance service is healthy.
    
    Returns:
        Tuple (healthy, message)
    """
    try:
        response = requests.get(
            f"{PROVENANCE_SERVICE_URL}/health",
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            return True, f"Provenance service is healthy (CBIR connected: {data.get('cbir_connected')})"
        return False, f"Provenance service returned status {response.status_code}"
    except requests.RequestException as e:
        return False, f"Failed to connect to Provenance service: {str(e)}"


def analyze_provenance(
    user_id: str,
    images: List[Dict[str, Any]],
    query_image: Dict[str, Any],
    k: int = 10,
    q: int = 5,
    max_depth: int = 3,
    descriptor_type: str = "cv_rsift",
    max_workers: int = 4
) -> Tuple[bool, str, Dict]:
    """
    Analyze provenance for a query image against a set of available images.
    
    Args:
        user_id: User ID for CBIR multi-tenant isolation
        images: List of available images (id, path, label)
        query_image: The query image (id, path, label)
        k: Number of top candidates from initial CBIR search
        q: Number of candidates for expansion
        max_depth: Maximum expansion depth
        descriptor_type: Keypoint descriptor type
        max_workers: Parallel workers for extraction
        
    Returns:
        Tuple (success, message, result_data)
    """
    # Convert paths
    processed_images = []
    for img in images:
        processed_images.append({
            "id": str(img["id"]),
            "path": str(convert_host_path_to_container(img["path"])),
            "label": img.get("label", "")
        })
        
    processed_query = {
        "id": str(query_image["id"]),
        "path": str(convert_host_path_to_container(query_image["path"])),
        "label": query_image.get("label", "")
    }
    
    payload = {
        "user_id": user_id,
        "images": processed_images,
        "query_image": processed_query,
        "k": k,
        "q": q,
        "max_depth": max_depth,
        "descriptor_type": descriptor_type,
        "max_workers": max_workers
    }
    
    logger.info("Starting provenance analysis for user %s, query %s", user_id, query_image['id'])
    
    try:
        response = requests.post(
            f"{PROVENANCE_SERVICE_URL}/analyze",
            json=payload,
            timeout=PROVENANCE_TIMEOUT
        )
        
        if response.status_code == 200:
            return True, "Analysis completed successfully", response.json()
        try:
            error_detail = response.json().get("detail", response.text)
        except ValueError:
            error_detail = response.text
        logger.error("Provenance analysis failed: %s", error_detail)
        return False, f"Analysis failed: {error_detail}", {}

    except requests.ConnectionError as e:
        # Service not reachable (e.g. still starting): let the task retry
        raise TransientError(f"Provenance service unreachable: {e}") from e
    except requests.RequestException as e:
        logger.error("Provenance service request failed: %s", e)
        return False, f"Provenance service error: {str(e)}", {}
