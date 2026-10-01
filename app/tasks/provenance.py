"""
Provenance Analysis Tasks

Celery tasks for handling background provenance analysis.
"""
from app.celery_config import celery_app
from app.services.provenance_service import run_provenance_analysis
from app.services.relationship_service import create_relationship
from app.schemas import JobType
from app.tasks.lifecycle import TrackedJob, run_analysis
from app.config.settings import CELERY_MAX_RETRIES
import logging

logger = logging.getLogger(__name__)


def _create_relationships_from_provenance(user_id: str, query_image_id: str, result: dict, analysis_id: str):
    """
    Create relationships from provenance analysis results.
    Uses edges from the provenance graph to establish image relationships.
    
    The provenance result contains:
    - graph_edges: List of {from, to, weight, ...} edge objects
    - spanning_tree_edges: MST edges (also valid for relationships)
    """
    try:
        # Debug: log the result keys to understand structure
        logger.info(f"Provenance result keys: {list(result.keys()) if result else 'None'}")
        
        # The provenance result has edges nested under 'graph' key
        graph = result.get('graph', {})
        edges = graph.get('edges', [])
        
        # Fallback: try other locations
        if not edges:
            edges = graph.get('spanning_tree_edges', [])
        if not edges:
            edges = result.get('graph_edges', [])
        if not edges:
            edges = result.get('edges', [])
        
        logger.info(f"Found {len(edges)} edges for analysis {analysis_id}")
        
        if not edges:
            logger.info(f"No edges found in provenance result for analysis {analysis_id}")
            return 0
        
        created_count = 0
        for edge in edges:
            # Handle different field name conventions
            source_id = edge.get('from') or edge.get('source') or edge.get('image1_id')
            target_id = edge.get('to') or edge.get('target') or edge.get('image2_id')
            weight = edge.get('weight', 1.0)
            if not source_id or not target_id or source_id == target_id:
                continue
            try:
                create_relationship(
                    user_id=user_id,
                    image1_id=source_id,
                    image2_id=target_id,
                    source_type='provenance',
                    weight=weight,
                    metadata={'analysis_id': analysis_id}
                )
                created_count += 1
            except Exception as e:
                logger.warning(f"Could not create relationship {source_id}-{target_id}: {e}")

        logger.info(f"Created {created_count} relationships from provenance analysis {analysis_id}")
        return created_count
    except Exception as e:
        logger.error(f"Error creating relationships from provenance: {e}")
        return 0


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.provenance_analysis")
def provenance_analysis_task(
    self,
    analysis_id: str,
    user_id: str,
    query_image_id: str,
    search_image_ids: list = None,
    k: int = 10,
    q: int = 5,
    max_depth: int = 3,
    descriptor_type: str = "cv_rsift",
    job_id: str = None
):
    """
    Run provenance analysis asynchronously and record image relationships
    from the resulting graph.

    Args:
        analysis_id: MongoDB analysis ID
        user_id: User ID
        query_image_id: Query image ID
        search_image_ids: Optional subset of images to search
        k: Top-K candidates
        q: Top-Q expansion
        max_depth: Expansion depth
        descriptor_type: Descriptor type
        job_id: Pre-created job ID from the route (for pending state tracking)
    """
    job = TrackedJob.ensure(
        self, user_id, job_id, JobType.PROVENANCE, "Provenance Analysis",
        {"query_image_id": query_image_id, "analysis_id": analysis_id, "k": k, "q": q, "max_depth": max_depth},
        analysis_id=analysis_id,
    )

    def work():
        success, message, result = run_provenance_analysis(
            user_id=user_id,
            query_image_id=query_image_id,
            search_image_ids=search_image_ids,
            k=k,
            q=q,
            max_depth=max_depth,
            descriptor_type=descriptor_type
        )
        if not success:
            return False, message, None
        result = result or {}
        result["relationships_created"] = _create_relationships_from_provenance(
            user_id=user_id,
            query_image_id=query_image_id,
            result=result,
            analysis_id=analysis_id
        )
        return True, message, result

    return run_analysis(
        self, job, "Running provenance analysis...", work,
        output_data=lambda result: {"relationships_created": result.get("relationships_created", 0)},
    )
