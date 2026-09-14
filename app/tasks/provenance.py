"""
Provenance Analysis Tasks

Celery tasks for handling background provenance analysis.
"""
from app.celery_config import celery_app
from app.db.mongodb import get_analyses_collection
from app.services.provenance_service import run_provenance_analysis
from app.services.relationship_service import create_relationship
from app.schemas import AnalysisStatus, JobType, JobStatus
from app.services.job_logger import create_job_log, update_job_progress, complete_job
from app.config.settings import CELERY_MAX_RETRIES
from bson import ObjectId
from datetime import datetime
import asyncio
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
        
        if edges:
            async def process_edges():
                tasks = []
                for edge in edges:
                    # Handle different field name conventions
                    source_id = edge.get('from') or edge.get('source') or edge.get('image1_id')
                    target_id = edge.get('to') or edge.get('target') or edge.get('image2_id')
                    weight = edge.get('weight', 1.0)
                    
                    if source_id and target_id and source_id != target_id:
                        tasks.append(
                            create_relationship(
                                user_id=user_id,
                                image1_id=source_id,
                                image2_id=target_id,
                                source_type='provenance',
                                weight=weight,
                                metadata={'analysis_id': analysis_id}
                            )
                        )
                
                # Execute all creations concurrently
                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    # Count successes
                    return sum(1 for r in results if not isinstance(r, Exception))
                return 0

            try:
                # Use asyncio.run to execute the async function in this synchronous context
                created_count = asyncio.run(process_edges())
                logger.info(f"Created {created_count} relationships from provenance analysis {analysis_id}")
            except Exception as e:
                logger.error(f"Failed to execute async relationship creation: {e}")
        else:
             logger.info(f"No edges to process for analysis {analysis_id}")
        
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
    Run provenance analysis asynchronously.
    
    Args:
        analysis_id: MongoDB analysis ID
        user_id: User ID
        query_image_id: Query image ID
        k: Top-K candidates
        q: Top-Q expansion
        max_depth: Expansion depth
        descriptor_type: Descriptor type
        job_id: Optional pre-created job ID from the route (for pending state tracking)
    """
    analyses_col = get_analyses_collection()
    
    try:
        # Use provided job_id or create one if not provided (backward compatibility)
        if not job_id:
            job_id = create_job_log(
                user_id=user_id,
                job_type=JobType.PROVENANCE,
                title="Provenance Analysis",
                celery_task_id=self.request.id,
                input_data={
                    "query_image_id": query_image_id,
                    "analysis_id": analysis_id,
                    "k": k,
                    "q": q,
                    "max_depth": max_depth
                }
            )
        
        # Update status to processing
        update_job_progress(job_id, user_id, JobStatus.PROCESSING, 10, "Running provenance analysis...")
        analyses_col.update_one(
            {"_id": ObjectId(analysis_id)},
            {
                "$set": {
                    "status": AnalysisStatus.PROCESSING,
                    "status_message": "Running provenance analysis...",
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        logger.info(f"Starting provenance analysis {analysis_id} for user {user_id}")
        
        success, message, result = run_provenance_analysis(
            user_id=user_id,
            query_image_id=query_image_id,
            search_image_ids=search_image_ids,
            k=k,
            q=q,
            max_depth=max_depth,
            descriptor_type=descriptor_type
        )
        
        if success:
            # Create relationships from provenance edges
            relationships_created = _create_relationships_from_provenance(
                user_id=user_id,
                query_image_id=query_image_id,
                result=result,
                analysis_id=analysis_id
            )
            
            # Add relationship count to results
            if result:
                result['relationships_created'] = relationships_created
            
            # Store results
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.COMPLETED,
                        "status_message": "Completed",
                        "results": result,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            complete_job(job_id, user_id, JobStatus.COMPLETED, {"analysis_id": analysis_id, "relationships_created": relationships_created})
            logger.info(f"Provenance analysis {analysis_id} completed successfully")
            return {"status": "completed", "result": result}
        else:
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.FAILED,
                        "error": message,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[message])
            logger.error(f"Provenance analysis {analysis_id} failed: {message}")
            return {"status": "failed", "error": message}
    except Exception as e:
        logger.error(f"Error in provenance analysis {analysis_id}: {e}")
        analyses_col.update_one(
            {"_id": ObjectId(analysis_id)},
            {
                "$set": {
                    "status": AnalysisStatus.FAILED,
                    "error": str(e),
                    "updated_at": datetime.utcnow()
                }
            }
        )
        if job_id:
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[str(e)])
        raise self.retry(exc=e, countdown=60)
