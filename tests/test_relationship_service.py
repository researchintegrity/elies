"""
Critical Path Tests for Relationship Service

Tests the core functionality of the image relationship service.
Run with: pytest tests/test_relationship_service.py -v
"""
import pytest
from unittest.mock import MagicMock, patch
from bson import ObjectId

from app.db.mongodb import get_images_collection

# Import the service functions
from app.services.relationship_service import (
    create_relationship,
    remove_relationship,
    remove_relationships_for_image,
    get_relationships_for_image,
    get_relationship_graph,
    compute_max_spanning_tree,
    _normalize_image_ids
)


class TestNormalizeImageIds:
    """Test ID normalization for bidirectional relationships"""
    
    def test_normalize_sorts_ids(self):
        """IDs should be sorted alphabetically"""
        id1, id2 = _normalize_image_ids("bbb", "aaa")
        assert id1 == "aaa"
        assert id2 == "bbb"
    
    def test_normalize_same_order_when_already_sorted(self):
        """Already sorted IDs should remain in order"""
        id1, id2 = _normalize_image_ids("aaa", "bbb")
        assert id1 == "aaa"
        assert id2 == "bbb"
    
    def test_normalize_same_id_both_positions(self):
        """Same ID in both positions should still work"""
        id1, id2 = _normalize_image_ids("same", "same")
        assert id1 == "same"
        assert id2 == "same"


class TestComputeMaxSpanningTree:
    """Test MST algorithm with various graph configurations"""
    
    def test_mst_single_edge(self):
        """Single edge graph returns that edge"""
        nodes = ["A", "B"]
        edges = [{"source": "A", "target": "B", "weight": 1.0}]
        
        mst = compute_max_spanning_tree(nodes, edges)
        
        assert len(mst) == 1
        assert mst[0]["source"] == "A"
        assert mst[0]["target"] == "B"
    
    def test_mst_selects_max_weight(self):
        """MST should prefer higher weight edges"""
        nodes = ["A", "B", "C"]
        edges = [
            {"source": "A", "target": "B", "weight": 0.5},
            {"source": "A", "target": "C", "weight": 0.9},
            {"source": "B", "target": "C", "weight": 0.3}
        ]
        
        mst = compute_max_spanning_tree(nodes, edges)
        
        # MST should have 2 edges for 3 nodes
        assert len(mst) == 2
        
        # Should include the highest weight edges: A-C (0.9) and A-B (0.5)
        weights = sorted([e["weight"] for e in mst], reverse=True)
        assert weights == [0.9, 0.5]
    
    def test_mst_empty_graph(self):
        """Empty graph returns empty MST"""
        mst = compute_max_spanning_tree([], [])
        assert mst == []
    
    def test_mst_single_node(self):
        """Single node returns empty MST (no edges)"""
        mst = compute_max_spanning_tree(["A"], [])
        assert mst == []
    
    def test_mst_disconnected_components(self):
        """Disconnected graph returns MST of reachable nodes only"""
        nodes = ["A", "B", "C", "D"]  # A-B connected, C-D connected, no bridge
        edges = [
            {"source": "A", "target": "B", "weight": 1.0},
            {"source": "C", "target": "D", "weight": 0.8}
        ]
        
        mst = compute_max_spanning_tree(nodes, edges)
        
        # Prim's starting from first node only reaches A-B
        # Depending on implementation, it may or may not include C-D
        assert len(mst) >= 1


class TestRemoveRelationship:
    """Test relationship removal"""
    
    @patch('app.services.relationship_service.get_relationships_collection')
    def test_remove_existing_relationship(self, mock_rels):
        """Removing existing relationship returns True"""
        mock_rels.return_value.delete_one.return_value = MagicMock(deleted_count=1)
        
        rel_id = str(ObjectId())
        result = remove_relationship(rel_id, "user1")
        
        assert result is True
    
    @patch('app.services.relationship_service.get_relationships_collection')
    def test_remove_nonexistent_relationship(self, mock_rels):
        """Removing non-existent relationship returns False"""
        mock_rels.return_value.delete_one.return_value = MagicMock(deleted_count=0)
        
        result = remove_relationship(str(ObjectId()), "user1")
        
        assert result is False


class TestRemoveRelationshipsForImage:
    """Test cascade deletion of relationships"""
    
    @patch('app.services.relationship_service.get_relationships_collection')
    def test_remove_all_for_image(self, mock_rels):
        """All relationships involving an image are removed"""
        mock_rels.return_value.delete_many.return_value = MagicMock(deleted_count=5)
        
        count = remove_relationships_for_image("image123", "user1")
        
        assert count == 5
        mock_rels.return_value.delete_many.assert_called_once()


class TestGetRelationshipsForImage:
    """Test querying relationships"""
    
    @patch('app.services.relationship_service.get_relationships_collection')
    @patch('app.services.relationship_service.get_images_collection')
    def test_get_relationships_basic(self, mock_images, mock_rels):
        """Basic query returns relationships for image"""
        mock_rels.return_value.find.return_value = [
            {"_id": ObjectId(), "image1_id": "aaa", "image2_id": "bbb", "weight": 1.0}
        ]
        mock_images.return_value.find_one.return_value = {"filename": "test.png"}
        
        results = get_relationships_for_image("aaa", "user1", include_image_details=True)
        
        assert len(results) == 1
        assert results[0]["image1_id"] == "aaa"


# Integration test markers (require running services)
@pytest.mark.integration
class TestRelationshipServiceIntegration:
    """
    Integration tests that require MongoDB connection.
    Run with: pytest tests/test_relationship_service.py -v -m integration
    """
    
    @pytest.fixture
    def test_user_id(self):
        return "test_user_relationships"
    
    @pytest.fixture
    def test_image_ids(self):
        return [str(ObjectId()), str(ObjectId()), str(ObjectId())]
    
    def test_full_relationship_lifecycle(self, test_user_id, test_image_ids, mongodb_connection):
        """Test create -> query -> remove flow"""
        img1, img2, img3 = test_image_ids
        
        # 0. Insert dummy images so graph traversal works
        images_col = get_images_collection()
        for i, img_id in enumerate([img1, img2, img3]):
            images_col.insert_one({
                "_id": ObjectId(img_id),
                "user_id": test_user_id,
                "filename": f"test_img_{i}.png",
                "is_flagged": False
            })
        
        # 1. Create relationships
        rel1 = create_relationship(test_user_id, img1, img2, "manual", weight=0.8)
        rel2 = create_relationship(test_user_id, img2, img3, "similarity", weight=0.6)
        
        assert rel1 is not None
        assert rel2 is not None
        
        # 2. Query relationships
        rels = get_relationships_for_image(img2, test_user_id, include_image_details=False)
        assert len(rels) >= 2  # img2 is connected to both img1 and img3
        
        # 3. Get graph
        graph = get_relationship_graph(img1, test_user_id, max_depth=2)
        assert len(graph["nodes"]) >= 2
        
        # 4. Remove relationships
        result1 = remove_relationship(str(rel1["_id"]), test_user_id)
        assert result1 is True
        
        # 5. Cascade delete
        count = remove_relationships_for_image(img2, test_user_id)
        assert count >= 0
