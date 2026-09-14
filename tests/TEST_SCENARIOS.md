# ELIES System Test Coverage Overview

## Quick Reference: What We Test

### User Management (`test_user_operations.py`)
- **Registration**: Valid/invalid data, duplicates, validation
- **Authentication**: Login with username/email, token validation
- **Account Management**: User deletion, lifecycle testing
- **Integration**: Multi-user scenarios, independence
- **Coverage**: 17 tests ✅

### Document Operations (`test_document_upload.py`, `test_deletion_e2e.py`)
- **Upload**: PDF/image file handling, validation, storage
- **Deletion**: File removal, database cleanup, path resolution
- **Storage**: Quota management, file system operations
- **Coverage**: Upload + delete workflows ✅

### Content Analysis
- **CBIR**: Content-based image retrieval (`test_cbir_unit.py`, `test_cbir_e2e.py`)
- **Copy-Move Detection**: Image manipulation detection (`test_copy_move_e2e.py`)
- **Panel Extraction**: Figure extraction from PDFs (`test_panel_extraction*.py`)
- **Provenance Analysis**: Document authenticity (`test_provenance*.py`)
- **TRUFOR**: Image forensics (`test_trufor*.py`)
- **Watermark Removal**: PDF cleaning (`test_watermark_removal.py`)

### Docker Integration (`test_docker_extraction.py`)
- **Container Operations**: Docker-based processing workflows
- **Path Mapping**: Host ↔ container file system translation
- **Error Handling**: Timeout, missing files, invalid parameters

## Test Status Summary

| Component | Tests | Status | Notes |
|-----------|-------|--------|-------|
| User Operations | 17 | ✅ | Complete coverage |
| Document Upload/Delete | 4 | ✅ | E2E workflows |
| CBIR System | 12 | 🔄 | Unit + E2E |
| Copy-Move Detection | 8 | 🔄 | E2E focused |
| Panel Extraction | 15 | 🔄 | Docker + E2E |
| Provenance Analysis | 10 | 🔄 | Integration tests |
| TRUFOR | 6 | 🔄 | E2E + timeout |
| Watermark Removal | 13 | ✅ | Fixed recently |
| Docker Utils | 5 | 🔄 | Path resolution |

## Key Test Scenarios

### Core Workflows
1. **User Lifecycle**: Register → Login → Upload → Process → Delete → Cleanup
2. **Document Processing**: Upload → Extract → Analyze → Store results
3. **Error Recovery**: Invalid files, timeouts, quota exceeded, auth failures

### Environment Handling
- **Host vs Container**: Path resolution, volume mounting
- **Database**: MongoDB operations, cleanup between tests
- **Docker**: Container lifecycle, error propagation

### Data Validation
- **File Types**: PDF, images (PNG/JPG/GIF/WebP/BMP)
- **Security**: JWT tokens, user isolation, input sanitization
- **Business Rules**: Storage quotas, duplicate prevention

## Running Tests

```bash
# All tests
pytest tests/ -v

# Specific component
pytest tests/test_user_operations.py -v
pytest tests/test_deletion_e2e.py -v

# With coverage
pytest tests/ --cov=app --cov-report=html
```