"""
Docker integration tests for panel extraction.

Tests the actual Docker container execution with real panel-extractor image.
"""

import pytest
import tempfile
import os
import shutil
import subprocess
from pathlib import Path

# Needs a Docker daemon and the built tool images (docker compose --profile tools build)
pytestmark = pytest.mark.e2e

# These tests require:
# 1. Docker daemon running
# 2. panel-extractor:latest image available
# 3. Sample test images


class TestDockerIntegration:
    """Integration tests with actual Docker container."""

    @pytest.fixture
    def test_image_paths(self):
        """Create temporary test images for panel extraction."""
        # Use the sample images from panel-extractor repo
        sample_images = [
            "/media/jcardenuto/Windows/Users/phill/work/2025-elis-system/system_modules/panel-extractor/fig1.png",
        ]

        yield sample_images

    @pytest.fixture
    def temp_output_dir(self):
        """Create temporary output directory."""
        temp_dir = tempfile.mkdtemp(prefix="panel_extraction_test_")
        yield temp_dir
        # Cleanup
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    def test_docker_image_exists(self):
        """Test that panel-extractor:latest Docker image exists."""
        result = subprocess.run(
            ["docker", "images", "panel-extractor:latest"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "panel-extractor" in result.stdout

    def test_docker_help_command(self):
        """Test that panel-extractor Docker container runs with --help."""
        result = subprocess.run(
            ["docker", "run", "--rm", "panel-extractor:latest", "--help"],
            capture_output=True,
            text=True,
            timeout=30
        )
        assert result.returncode == 0
        assert "--input" in result.stdout
        assert "--output" in result.stdout

    def test_docker_panel_extraction_basic(self, test_image_paths, temp_output_dir):
        """Test basic panel extraction with Docker container."""
        if not os.path.exists(test_image_paths[0]):
            pytest.skip(f"Test image not found: {test_image_paths[0]}")

        # Copy test image to temp directory
        temp_image_dir = os.path.join(temp_output_dir, "input")
        os.makedirs(temp_image_dir)
        shutil.copy(test_image_paths[0], temp_image_dir)

        temp_output_subdir = os.path.join(temp_output_dir, "output")
        os.makedirs(temp_output_subdir)

        # Run Docker container
        docker_command = [
            "docker",
            "run",
            "--rm",
            "-v", f"{temp_image_dir}:/workspace/input",
            "-v", f"{temp_output_subdir}:/workspace/output",
            "panel-extractor:latest",
            "--input-path", "/workspace/input/fig1.png",
            "--output-path", "/workspace/output",
        ]

        result = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=120
        )

        # Check execution
        assert result.returncode == 0, f"Docker failed: {result.stderr}"
        assert os.path.exists(os.path.join(temp_output_subdir, "PANELS.csv")), \
            "PANELS.csv was not generated"

    def test_docker_panels_csv_format(self, test_image_paths, temp_output_dir):
        """Test that PANELS.csv has the correct format."""
        if not os.path.exists(test_image_paths[0]):
            pytest.skip(f"Test image not found: {test_image_paths[0]}")

        # Setup directories
        temp_image_dir = os.path.join(temp_output_dir, "input")
        os.makedirs(temp_image_dir)
        shutil.copy(test_image_paths[0], temp_image_dir)

        temp_output_subdir = os.path.join(temp_output_dir, "output")
        os.makedirs(temp_output_subdir)

        # Run Docker container
        docker_command = [
            "docker",
            "run",
            "--rm",
            "-v", f"{temp_image_dir}:/workspace/input",
            "-v", f"{temp_output_subdir}:/workspace/output",
            "panel-extractor:latest",
            "--input-path", "/workspace/input/fig1.png",
            "--output-path", "/workspace/output",
        ]

        result = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=120
        )

        assert result.returncode == 0

        # Verify PANELS.csv format
        csv_path = os.path.join(temp_output_subdir, "PANELS.csv")
        with open(csv_path, 'r') as f:
            lines = f.readlines()

        # Check header
        header = lines[0].strip()
        expected_columns = ["FIGNAME", "ID", "LABEL", "X0", "Y0", "X1", "Y1"]
        for col in expected_columns:
            assert col in header, f"Column {col} not found in CSV header"

        # Check data rows
        if len(lines) > 1:
            for row_num, line in enumerate(lines[1:], start=2):
                columns = line.strip().split(',')
                # CSV should have at least 7 columns
                assert len(columns) >= 7, f"Row {row_num} has insufficient columns"

    def test_docker_extracted_images_created(self, test_image_paths, temp_output_dir):
        """Test that extracted panel images are created."""
        if not os.path.exists(test_image_paths[0]):
            pytest.skip(f"Test image not found: {test_image_paths[0]}")

        # Setup directories
        temp_image_dir = os.path.join(temp_output_dir, "input")
        os.makedirs(temp_image_dir)
        shutil.copy(test_image_paths[0], temp_image_dir)

        temp_output_subdir = os.path.join(temp_output_dir, "output")
        os.makedirs(temp_output_subdir)

        # Run Docker container
        docker_command = [
            "docker",
            "run",
            "--rm",
            "-v", f"{temp_image_dir}:/workspace/input",
            "-v", f"{temp_output_subdir}:/workspace/output",
            "panel-extractor:latest",
            "--input-path", "/workspace/input/fig1.png",
            "--output-path", "/workspace/output",
        ]

        result = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=120
        )

        assert result.returncode == 0

        # Check that panel images were created
        output_files = os.listdir(temp_output_subdir)
        png_files = [f for f in output_files if f.endswith('.png')]

        # Should have extracted at least one panel
        assert len(png_files) > 0, "No extracted panel images found"

        # Each PNG should be a valid file
        for png_file in png_files:
            file_path = os.path.join(temp_output_subdir, png_file)
            assert os.path.getsize(file_path) > 0, f"Panel image is empty: {png_file}"

    def test_docker_handles_missing_input_gracefully(self, temp_output_dir):
        """Test that Docker handles missing input gracefully (with warning)."""
        temp_output_subdir = os.path.join(temp_output_dir, "output")
        os.makedirs(temp_output_subdir)

        # Create empty input directory
        temp_image_dir = os.path.join(temp_output_dir, "input")
        os.makedirs(temp_image_dir)

        # Run Docker with no images
        docker_command = [
            "docker",
            "run",
            "--rm",
            "-v", f"{temp_image_dir}:/workspace/input",
            "-v", f"{temp_output_subdir}:/workspace/output",
            "panel-extractor:latest",
            "--input-path", "/workspace/input/nonexistent.png",
            "--output-path", "/workspace/output",
        ]

        result = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=120
        )

        # Docker may succeed with warning or fail gracefully
        # Either way, it should not extract any panels
        assert (result.returncode != 0 or "Warning" in result.stdout or "Warninig" in result.stdout), \
            "Docker should warn or fail on missing input"


class TestPanelExtractionWorkflow:
    """End-to-end workflow tests."""

    def test_docker_to_csv_workflow(self):
        """Test complete workflow: Docker execution -> CSV parsing."""
        sample_image = "/media/jcardenuto/Windows/Users/phill/work/2025-elis-system/system_modules/panel-extractor/fig1.png"

        if not os.path.exists(sample_image):
            pytest.skip(f"Test image not found: {sample_image}")

        with tempfile.TemporaryDirectory() as temp_dir:
            # Setup
            input_dir = os.path.join(temp_dir, "input")
            output_dir = os.path.join(temp_dir, "output")
            os.makedirs(input_dir)
            os.makedirs(output_dir)

            # Copy test image
            shutil.copy(sample_image, input_dir)

            # Execute Docker
            docker_command = [
                "docker",
                "run",
                "--rm",
                "-v", f"{input_dir}:/workspace/input",
                "-v", f"{output_dir}:/workspace/output",
                "panel-extractor:latest",
                "--input-path", "/workspace/input/fig1.png",
                "--output-path", "/workspace/output",
            ]

            result = subprocess.run(
                docker_command,
                capture_output=True,
                text=True,
                timeout=120
            )

            assert result.returncode == 0

            # Verify CSV exists and can be parsed
            csv_path = os.path.join(output_dir, "PANELS.csv")
            assert os.path.exists(csv_path)

            # Parse CSV
            import csv
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            # Verify we have panels
            assert len(rows) > 0, "No panels extracted"

            # Verify row structure
            for row in rows:
                assert "FIGNAME" in row
                assert "ID" in row
                assert "LABEL" in row
                assert "X0" in row and "Y0" in row and "X1" in row and "Y1" in row

                # Verify coordinates can be parsed as floats
                for coord_col in ["X0", "Y0", "X1", "Y1"]:
                    coord = float(row[coord_col].strip())
                    assert coord >= 0, f"Coordinate {coord_col} is negative: {coord}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
