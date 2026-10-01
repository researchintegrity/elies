"""
Image Relationship Service

Manages bidirectional relationships between images with graph operations.
Supports relationships from provenance analysis, cross copy-move detection,
similarity search, and manual annotation.
"""

from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime
from collections import defaultdict
from bson import ObjectId
import logging

from app.db.mongodb import (
    get_relationships_collection,
    get_images_collection
)

logger = logging.getLogger(__name__)


def _normalize_image_ids(image1_id: str, image2_id: str) -> Tuple[str, str]:
    """
    Normalize image IDs by sorting them.
    This ensures (A, B) and (B, A) are stored as the same relationship.
    """
    return tuple(sorted([image1_id, image2_id]))


def create_relationship(
    user_id: str,
    image1_id: str,
    image2_id: str,
    source_type: str,
    source_analysis_id: Optional[str] = None,
    weight: float = 1.0,
    metadata: Optional[Dict] = None,
    created_by: str = "system"
) -> Dict[str, Any]:
    """
    Create a bidirectional relationship between two images.
    
    - Normalizes image IDs (sorted) to prevent duplicates
    - ALWAYS flags both images when relationship is created
    - Returns existing relationship if already exists (upsert behavior)
    
    Args:
        user_id: Owner of the relationship
        image1_id: First image ID
        image2_id: Second image ID
        source_type: One of 'provenance', 'cross_copy_move', 'similarity', 'manual'
        source_analysis_id: Optional reference to analysis
        weight: Relationship strength (0-1, default 1.0)
        metadata: Additional context data
        created_by: 'system' or user_id for manual
    
    Returns:
        The created or existing relationship document
    """
    if image1_id == image2_id:
        raise ValueError("Cannot create relationship between an image and itself")
    
    # Normalize IDs for consistent storage
    norm_id1, norm_id2 = _normalize_image_ids(image1_id, image2_id)
    
    relationships_col = get_relationships_collection()
    images_col = get_images_collection()
    
    # Check if relationship already exists
    existing = relationships_col.find_one({
        "user_id": user_id,
        "image1_id": norm_id1,
        "image2_id": norm_id2
    })
    
    if existing:
        # Optionally update weight if new weight is higher
        if weight > existing.get("weight", 0):
            relationships_col.update_one(
                {"_id": existing["_id"]},
                {"$set": {"weight": weight, "metadata": metadata or existing.get("metadata")}}
            )
            existing["weight"] = weight
        existing["_id"] = str(existing["_id"])
        return existing
    
    # Create new relationship
    now = datetime.utcnow()
    relationship_doc = {
        "user_id": user_id,
        "image1_id": norm_id1,
        "image2_id": norm_id2,
        "source_type": source_type,
        "source_analysis_id": source_analysis_id,
        "weight": weight,
        "metadata": metadata or {},
        "created_at": now,
        "created_by": created_by
    }
    
    result = relationships_col.insert_one(relationship_doc)
    relationship_doc["_id"] = str(result.inserted_id)
    
    # Always flag both images when a relationship is created
    # This ensures related images are marked for review
    images_col.update_many(
        {"_id": {"$in": [ObjectId(norm_id1), ObjectId(norm_id2)]}, "user_id": user_id},
        {"$set": {"is_flagged": True}}
    )
    logger.info(f"Flagged images {norm_id1} and {norm_id2} due to new relationship")
    
    logger.info(f"Created relationship between {norm_id1} and {norm_id2} (source: {source_type})")
    return relationship_doc


def remove_relationship(
    relationship_id: str,
    user_id: str
) -> bool:
    """
    Remove a relationship by ID.
    
    Returns:
        True if deleted, False if not found
    """
    relationships_col = get_relationships_collection()
    
    try:
        result = relationships_col.delete_one({
            "_id": ObjectId(relationship_id),
            "user_id": user_id
        })
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Error removing relationship {relationship_id}: {e}")
        return False


def remove_relationships_for_image(
    image_id: str,
    user_id: str
) -> int:
    """
    Remove ALL relationships involving an image (cascade delete).
    Called when an image is deleted.
    
    Returns:
        Count of deleted relationships
    """
    relationships_col = get_relationships_collection()
    
    result = relationships_col.delete_many({
        "user_id": user_id,
        "$or": [
            {"image1_id": image_id},
            {"image2_id": image_id}
        ]
    })
    
    if result.deleted_count > 0:
        logger.info(f"Cascade deleted {result.deleted_count} relationships for image {image_id}")
    
    return result.deleted_count


def get_relationships_for_image(
    image_id: str,
    user_id: str,
    include_image_details: bool = True
) -> List[Dict[str, Any]]:
    """
    Get all relationships for a specific image.
    
    Args:
        image_id: Image to find relationships for
        user_id: User who owns the relationships
        include_image_details: If True, enriches with other image's basic info
    
    Returns:
        List of relationship documents with optional other_image field
    """
    relationships_col = get_relationships_collection()
    images_col = get_images_collection()
    
    # Find relationships where this image is either image1 or image2
    relationships = list(relationships_col.find({
        "user_id": user_id,
        "$or": [
            {"image1_id": image_id},
            {"image2_id": image_id}
        ]
    }))
    
    # Enrich with other image details if requested
    if include_image_details and relationships:
        for rel in relationships:
            rel["_id"] = str(rel["_id"])
            # Determine which is the "other" image
            other_id = rel["image2_id"] if rel["image1_id"] == image_id else rel["image1_id"]
            
            try:
                other_img = images_col.find_one(
                    {"_id": ObjectId(other_id), "user_id": user_id},
                    {"filename": 1, "is_flagged": 1, "file_size": 1, "uploaded_date": 1}
                )
                if other_img:
                    rel["other_image"] = {
                        "id": str(other_img["_id"]),
                        "filename": other_img.get("filename", "Unknown"),
                        "is_flagged": other_img.get("is_flagged", False),
                        "file_size": other_img.get("file_size", 0)
                    }
            except Exception:
                rel["other_image"] = None
    else:
        for rel in relationships:
            rel["_id"] = str(rel["_id"])
    
    return relationships


def get_relationship_graph(
    image_id: str,
    user_id: str,
    max_depth: int = 5
) -> Dict[str, Any]:
    """
    Build the full relationship graph starting from an image using BFS.
    
    Args:
        image_id: Starting image for graph exploration
        user_id: User who owns the relationships
        max_depth: Maximum BFS depth (default 5, 0 = unlimited)
    
    Returns:
        Dictionary with nodes, edges, and mst_edges for visualization
    """
    relationships_col = get_relationships_collection()
    images_col = get_images_collection()
    
    # BFS to explore the graph
    visited: Set[str] = set()
    queue: List[Tuple[str, int]] = [(image_id, 0)]  # (image_id, depth)
    nodes_map: Dict[str, Dict] = {}
    edges: List[Dict] = []
    edge_set: Set[Tuple[str, str]] = set()  # To prevent duplicate edges
    
    # Store all visited IDs to count total connected component size
    all_connected_count = 0
    
    while queue:
        current_id, depth = queue.pop(0)
        
        if current_id in visited:
            continue
        visited.add(current_id)
        all_connected_count += 1
        
        # Determine if this node should be part of the visualization graph
        include_in_graph = (max_depth == 0) or (depth <= max_depth)
        
        # Get image info for node ONLY if in graph
        if include_in_graph:
            try:
                img = images_col.find_one(
                    {"_id": ObjectId(current_id), "user_id": user_id},
                    {"filename": 1, "is_flagged": 1}
                )
                if img:
                    nodes_map[current_id] = {
                        "id": current_id,
                        "label": img.get("filename", f"Image {current_id[-6:]}"),
                        "is_flagged": img.get("is_flagged", False),
                        "is_query": current_id == image_id
                    }
            except Exception:
                nodes_map[current_id] = {
                    "id": current_id,
                    "label": f"Image {current_id[-6:]}",
                    "is_flagged": False,
                    "is_query": current_id == image_id
                }
        
        # Get relationships from this node (always fetch to traverse full component)
        rels = list(relationships_col.find({
            "user_id": user_id,
            "$or": [
                {"image1_id": current_id},
                {"image2_id": current_id}
            ]
        }))
        
        if include_in_graph:
            logger.info(f"BFS depth {depth}: Node {current_id[-8:]} has {len(rels)} relationships")
        
        for rel in rels:
            other_id = rel["image2_id"] if rel["image1_id"] == current_id else rel["image1_id"]
            
            # Check if we should include this edge in visualization
            # Include edge if source is within exploration range AND max_depth logic holds
            # (We only add edges extending FROM current node if we are strictly less than max_depth)
            include_edge = (max_depth == 0) or (depth < max_depth)
            
            if include_edge:
                # Create edge (normalized to prevent duplicates)
                edge_key = tuple(sorted([current_id, other_id]))
                if edge_key not in edge_set:
                    edge_set.add(edge_key)
                    edges.append({
                        "source": edge_key[0],
                        "target": edge_key[1],
                        "weight": rel.get("weight", 1.0),
                        "source_type": rel.get("source_type", "manual"),
                        "id": str(rel["_id"]),
                        "is_mst_edge": False  # Updated after MST computation
                    })
            
            # Add to queue for exploration (continue traversing even if not including in graph)
            if other_id not in visited:
                # Check if already in queue to avoid duplicates in queue?
                # Using visited set check at pop time handles loops, but checking queue could save memory.
                # For now, simple standard BFS is fine.
                queue.append((other_id, depth + 1))
                if include_in_graph:
                     logger.debug(f"  -> Added {other_id[-8:]} to queue at depth {depth + 1}")

    logger.info(f"Graph for {image_id[-8:]}: {len(nodes_map)} nodes, {len(edges)} edges. Total connected: {all_connected_count}")
    
    # Compute Maximum Spanning Tree
    mst_edges = compute_max_spanning_tree(list(nodes_map.keys()), edges)
    
    # Mark MST edges in the edges list
    mst_edge_set = {(e["source"], e["target"]) for e in mst_edges}
    for edge in edges:
        edge_key = (edge["source"], edge["target"])
        edge["is_mst_edge"] = edge_key in mst_edge_set
    
    return {
        "query_image_id": image_id,
        "nodes": list(nodes_map.values()),
        "edges": edges,
        "mst_edges": mst_edges,
        "total_nodes_count": all_connected_count
    }


def compute_max_spanning_tree(nodes: List[str], edges: List[Dict]) -> List[Dict]:
    """
    Compute Maximum Spanning Tree using Prim's algorithm.
    
    Args:
        nodes: List of node IDs
        edges: List of edge dictionaries with source, target, weight
    
    Returns:
        List of edges that form the Maximum Spanning Tree
    """
    if not nodes or not edges:
        return []
    
    # Build adjacency list
    adj: Dict[str, List[Tuple[str, float, Dict]]] = defaultdict(list)
    for edge in edges:
        src, tgt, weight = edge["source"], edge["target"], edge.get("weight", 1.0)
        adj[src].append((tgt, weight, edge))
        adj[tgt].append((src, weight, edge))
    
    # Prim's algorithm for Maximum Spanning Tree
    mst_edges: List[Dict] = []
    in_mst: Set[str] = set()
    
    # Start from first node
    start_node = nodes[0]
    in_mst.add(start_node)
    
    # Priority queue: (negative weight for max heap behavior, edge)
    # Using list and manual sorting since heapq is min-heap
    candidates: List[Tuple[float, Dict]] = []
    for neighbor, weight, edge in adj[start_node]:
        candidates.append((weight, edge))
    
    while candidates and len(in_mst) < len(nodes):
        # Sort by weight descending (maximum spanning tree)
        candidates.sort(key=lambda x: -x[0])
        
        # Find the maximum weight edge connecting to unvisited node
        for i, (weight, edge) in enumerate(candidates):
            src, tgt = edge["source"], edge["target"]
            new_node = tgt if src in in_mst else src
            
            if new_node not in in_mst:
                # Add edge to MST
                mst_edge = edge.copy()
                mst_edge["is_mst_edge"] = True
                mst_edges.append(mst_edge)
                in_mst.add(new_node)
                
                # Add new candidates
                for neighbor, w, e in adj[new_node]:
                    if neighbor not in in_mst:
                        candidates.append((w, e))
                
                # Remove used candidate
                candidates.pop(i)
                break
        else:
            # No valid edge found, graph might be disconnected
            break
    
    return mst_edges
