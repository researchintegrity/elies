#!/bin/bash
# run_tests.sh - Simple test runner script

echo "╔═════════════════════════════════════════════╗"
echo "║  ELIES System - User Operations Test Suite  ║"
echo "╚═════════════════════════════════════════════╝"
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if pytest is available
if ! command -v pytest &> /dev/null; then
    echo -e "${YELLOW}pytest not found. Installing dependencies...${NC}"
    pip install -r requirements.txt
fi

# Menu
echo -e "${BLUE}Select test mode:${NC}"
echo "1) Run ALL tests (full suite)"
echo "2) Run Registration tests only"
echo "3) Run Login tests only"
echo "4) Run Deletion tests only"
echo "5) Run Integration tests only"
echo "6) Run with coverage report"
echo "7) Run single test (interactive)"
echo "8) Exit"
echo ""
read -p "Enter choice (1-8): " choice

case $choice in
    1)
        echo -e "${GREEN}Running all tests...${NC}"
        pytest tests/test_user_operations.py -v
        ;;
    2)
        echo -e "${GREEN}Running Registration tests...${NC}"
        pytest tests/test_user_operations.py::TestUserRegistration -v
        ;;
    3)
        echo -e "${GREEN}Running Login tests...${NC}"
        pytest tests/test_user_operations.py::TestUserLogin -v
        ;;
    4)
        echo -e "${GREEN}Running Deletion tests...${NC}"
        pytest tests/test_user_operations.py::TestUserDeletion -v
        ;;
    5)
        echo -e "${GREEN}Running Integration tests...${NC}"
        pytest tests/test_user_operations.py::TestUserOperationsIntegration -v
        ;;
    6)
        echo -e "${GREEN}Running tests with coverage...${NC}"
        pytest tests/test_user_operations.py --cov=app --cov-report=html -v
        echo -e "${GREEN}Coverage report generated in htmlcov/index.html${NC}"
        ;;
    7)
        echo "Available tests:"
        pytest tests/test_user_operations.py --collect-only -q
        echo ""
        read -p "Enter test name (e.g., TestUserRegistration::test_register_user_success): " test_name
        pytest tests/test_user_operations.py::${test_name} -v
        ;;
    8)
        echo -e "${YELLOW}Exiting...${NC}"
        exit 0
        ;;
    *)
        echo -e "${YELLOW}Invalid choice${NC}"
        exit 1
        ;;
esac
