import pytest
import os
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image
from app.utils.docker_trufor import run_trufor_detection_with_docker
from app.schemas import AnalysisType

# Needs a Docker daemon and the built tool images (docker compose --profile tools build)
pytestmark = pytest.mark.e2e

# Create a large dummy image for testing
@pytest.fixture
def large_image():
    # Create a 4000x4000 image to ensure processing takes > 1 second
    width, height = 4000, 4000
    # Create a solid color image (or random-ish if needed, but solid is fine for size)
    img = Image.new('RGB', (width, height), color='red')
    
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp)
        file_path = tmp.name
        
    yield file_path
    
    if os.path.exists(file_path):
        os.remove(file_path)

@pytest.fixture
def mock_env():
    """Setup mock environment with temporary workspace and short timeout"""
    with tempfile.TemporaryDirectory() as temp_workspace:
        workspace_path = Path(temp_workspace)
        
        # Patch UPLOAD_DIR in file_storage to use temp workspace
        # Patch timeout to 1 second
        # Disable GPU
        # Set WORKSPACE_PATH env var
        with patch('app.utils.file_storage.UPLOAD_DIR', workspace_path), \
             patch('app.utils.docker_trufor.TRUFOR_TIMEOUT', 1), \
             patch('app.utils.docker_trufor.TRUFOR_USE_GPU', False), \
             patch.dict(os.environ, {"WORKSPACE_PATH": str(workspace_path)}):
            yield workspace_path

@pytest.mark.asyncio
async def test_trufor_timeout(large_image, mock_env):
    """
    Test that the TruFor detection times out correctly when the timeout is set to a small value.
    """
    analysis_id = "test_timeout_analysis"
    user_id = "test_user"
    
    # Mock status callback to capture "TIMEOUT" status
    status_updates = []
    def status_callback(msg):
        status_updates.append(msg)
        print(f"Test Callback: {msg}")

    print(f"Using temp workspace: {mock_env}")
    
    start_time = time.time()
    
    success, message, results = run_trufor_detection_with_docker(
        analysis_id=analysis_id,
        user_id=user_id,
        image_path=large_image,
        status_callback=status_callback
    )
    
    end_time = time.time()
    duration = end_time - start_time
    
    print(f"Execution took {duration:.2f} seconds")
    print(f"Result: success={success}, message={message}")
    print(f"Status updates: {status_updates}")

    # Assertions
    assert success is False, "Detection should have failed due to timeout"
    
    # Check if we received the TIMEOUT status update
    # The run_trufor.py script prints "[STATUS] TIMEOUT" which docker_trufor.py parses
    assert "TIMEOUT" in status_updates, "Should have received TIMEOUT status update"
    
    # The duration should be roughly the timeout (plus some overhead)
    # It shouldn't take significantly longer than the timeout
    # (e.g. if it took 10s for a 1s timeout, something is wrong)
    assert duration < 10, "Execution took too long for a 1s timeout"

if __name__ == "__main__":
    # Manual run helper
    import asyncio
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        img_path = os.path.join(tmpdir, "large.png")
        Image.new('RGB', (4000, 4000)).save(img_path)
        asyncio.run(test_trufor_timeout(img_path))
