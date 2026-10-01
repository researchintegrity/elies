"""
Image Relationship Service

Manages bidirectional relationships between images with graph operations.
Supports relationships from provenance analysis, cross copy-move detection,
similarity search, and manual annotation.
"""

import heapq
import itertools
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config.settings import RELATIONSHIP_GRAPH_MAX_NODES
from app.db.mongodb import get_images_collection, get_relationships_collection

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
    key = {"user_id": user_id, "image1_id": norm_id1, "image2_id": norm_id2}

    # Atomic upsert (the unique index on the key makes concurrent creation safe)
    new_doc = {
        "source_type": source_type,
        "source_analysis_id": source_analysis_id,
        "weight": weight,
        "metadata": metadata or {},
        "created_at": datetime.utcnow(),
        "created_by": created_by,
    }
    for attempt in range(2):
        try:
            existing = relationships_col.find_one_and_update(
                key, {"$setOnInsert": new_doc}, upsert=True, return_document=ReturnDocument.BEFORE
            )
            break
        except DuplicateKeyError:
            if attempt:  # a concurrent insert won twice in a row: give up
                raise

    if existing is None:
        relationship_doc = relationships_col.find_one(key)
        # Always flag both images when a relationship is created
        # This ensures related images are marked for review
        _flag_images(user_id, [norm_id1, norm_id2])
        logger.info("Created relationship between %s and %s (source: %s)", norm_id1, norm_id2, source_type)
    else:
        relationship_doc = existing
        # Keep the strongest evidence: raise the weight if the new one is higher
        if weight > existing.get("weight", 0):
            relationships_col.update_one(
                {"_id": existing["_id"], "weight": {"$lt": weight}},
                {"$set": {"weight": weight, "metadata": metadata or existing.get("metadata")}},
            )
            relationship_doc["weight"] = weight

    relationship_doc["_id"] = str(relationship_doc["_id"])
    return relationship_doc


def _flag_images(user_id: str, image_ids: Iterable[str]) -> None:
    oids = [ObjectId(i) for i in image_ids if ObjectId.is_valid(i)]
    if oids:
        get_images_collection().update_many({"_id": {"$in": oids}, "user_id": user_id},
                                            {"$set": {"is_flagged": True}})


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
        logger.error("Error removing relationship %s: %s", relationship_id, e)
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
        logger.info("Cascade deleted %s relationships for image %s", result.deleted_count, image_id)
    
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
    
    for rel in relationships:
        rel["_id"] = str(rel["_id"])

    # Enrich with the other image's details (one query for all of them)
    if include_image_details and relationships:
        other_ids = [rel["image2_id"] if rel["image1_id"] == image_id else rel["image1_id"]
                     for rel in relationships]
        others = {
            str(img["_id"]): img
            for img in images_col.find(
                {"_id": {"$in": [ObjectId(i) for i in other_ids if ObjectId.is_valid(i)]}, "user_id": user_id},
                {"filename": 1, "original_filename": 1, "is_flagged": 1, "file_size": 1},
            )
        }
        for rel, other_id in zip(relationships, other_ids):
            other_img = others.get(other_id)
            rel["other_image"] = {
                "id": other_id,
                "filename": other_img.get("original_filename") or other_img.get("filename", "Unknown"),
                "is_flagged": other_img.get("is_flagged", False),
                "file_size": other_img.get("file_size", 0),
            } if other_img else None

    return relationships


def _neighbours_query(user_id: str, image_ids: List[str]) -> Dict[str, Any]:
    return {"user_id": user_id, "$or": [{"image1_id": {"$in": image_ids}}, {"image2_id": {"$in": image_ids}}]}


def get_relationship_graph(
    image_id: str,
    user_id: str,
    max_depth: int = 5,
    max_nodes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build the relationship graph around an image, breadth first.

    The traversal is batched by level (one relationships query per BFS level
    and one images query for all nodes), and stops after ``max_nodes`` images
    (RELATIONSHIP_GRAPH_MAX_NODES by default) so a huge component cannot
    exhaust the API (issue #72).

    Args:
        image_id: Starting image for graph exploration
        user_id: User who owns the relationships
        max_depth: Depth of the nodes and edges returned (0 = unlimited).
            The component is still explored further, up to ``max_nodes``,
            to report its size in ``total_nodes_count``.
        max_nodes: Override RELATIONSHIP_GRAPH_MAX_NODES

    Returns:
        Dictionary with nodes, edges, mst_edges, total_nodes_count (capped
        at max_nodes) and truncated (True if the cap was reached)
    """
    max_nodes = max_nodes or RELATIONSHIP_GRAPH_MAX_NODES
    relationships_col = get_relationships_collection()

    def in_graph(depth: int) -> bool:
        return max_depth == 0 or depth <= max_depth

    depth_of: Dict[str, int] = {image_id: 0}  # discovery order = BFS order
    edges: Dict[Tuple[str, str], Dict[str, Any]] = {}
    truncated = False
    frontier, depth = [image_id], 0

    while frontier:
        frontier_set = set(frontier)
        next_frontier: List[str] = []
        # Edges leaving nodes at depth < max_depth are shown (as before)
        keep_edges = max_depth == 0 or depth < max_depth
        rels = relationships_col.find(
            _neighbours_query(user_id, frontier),
            {"image1_id": 1, "image2_id": 1, "weight": 1, "source_type": 1},
        )
        for rel in rels:
            a, b = rel["image1_id"], rel["image2_id"]
            if keep_edges:
                edge_key = (a, b) if a <= b else (b, a)
                edges.setdefault(edge_key, {
                    "source": edge_key[0],
                    "target": edge_key[1],
                    "weight": rel.get("weight", 1.0),
                    "source_type": rel.get("source_type", "manual"),
                    "id": str(rel["_id"]),
                    "is_mst_edge": False,  # Updated after MST computation
                })
            for current, other in ((a, b), (b, a)):
                if current not in frontier_set or other in depth_of:
                    continue
                if len(depth_of) >= max_nodes:
                    truncated = True
                    continue
                depth_of[other] = depth + 1
                next_frontier.append(other)
        frontier, depth = next_frontier, depth + 1

    node_ids = [i for i, d in depth_of.items() if in_graph(d)]
    images = {
        str(img["_id"]): img
        for img in get_images_collection().find(
            {"_id": {"$in": [ObjectId(i) for i in node_ids if ObjectId.is_valid(i)]}, "user_id": user_id},
            {"filename": 1, "original_filename": 1, "is_flagged": 1},
        )
    }
    nodes = []
    for node_id in node_ids:
        img = images.get(node_id)
        if img is None and ObjectId.is_valid(node_id):
            continue  # image deleted or not the user's
        nodes.append({
            "id": node_id,
            "label": (img or {}).get("original_filename") or (img or {}).get("filename") or f"Image {node_id[-6:]}",
            "is_flagged": (img or {}).get("is_flagged", False),
            "is_query": node_id == image_id,
        })

    edge_list = list(edges.values())
    mst_edges = compute_max_spanning_tree([n["id"] for n in nodes], edge_list)
    mst_keys = {(e["source"], e["target"]) for e in mst_edges}
    for edge in edge_list:
        edge["is_mst_edge"] = (edge["source"], edge["target"]) in mst_keys

    logger.info("Graph for %s: %d nodes, %d edges, %d connected%s",
                image_id, len(nodes), len(edge_list), len(depth_of), " (truncated)" if truncated else "")
    return {
        "query_image_id": image_id,
        "nodes": nodes,
        "edges": edge_list,
        "mst_edges": mst_edges,
        "total_nodes_count": len(depth_of),
        "truncated": truncated,
    }


def compute_max_spanning_tree(nodes: List[str], edges: List[Dict]) -> List[Dict]:
    """
    Maximum spanning tree of the component containing ``nodes[0]`` (Prim's
    algorithm with a heap: O(E log E)).

    Args:
        nodes: List of node IDs; the tree grows from the first one
        edges: List of edge dictionaries with source, target, weight

    Returns:
        List of edges that form the Maximum Spanning Tree
    """
    if not nodes or not edges:
        return []

    adj: Dict[str, List[Tuple[str, float, Dict]]] = defaultdict(list)
    for edge in edges:
        src, tgt, weight = edge["source"], edge["target"], edge.get("weight", 1.0)
        adj[src].append((tgt, weight, edge))
        adj[tgt].append((src, weight, edge))

    mst_edges: List[Dict] = []
    in_mst: Set[str] = {nodes[0]}
    order = itertools.count()  # tie-breaker: dicts are not comparable
    heap: List[Tuple[float, int, str, Dict]] = []

    def push_edges(node: str) -> None:
        for neighbour, weight, edge in adj[node]:
            if neighbour not in in_mst:
                heapq.heappush(heap, (-weight, next(order), neighbour, edge))

    push_edges(nodes[0])
    while heap and len(in_mst) < len(nodes):
        _, _, new_node, edge = heapq.heappop(heap)
        if new_node in in_mst:
            continue
        in_mst.add(new_node)
        mst_edges.append({**edge, "is_mst_edge": True})
        push_edges(new_node)

    return mst_edges
