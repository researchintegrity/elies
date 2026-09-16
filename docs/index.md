# ELIES Scientific Image Analysis System

ELIES is a extendable and scalable system totally and forever open source dedicated to integrity analysis of research images and documents.



## Features

- **User Authentication**: Secure management with JWT tokens.
- **Document Management**: Upload and manage documents with storage quotas.
- **Asynchronous Processing**: Scalable PDF processing using Celery workers.
- **Image Extraction**: Extract images from scientific documents.
- **Panel Extraction**: YOLO-based panel extraction from scientific figures.
- **Integrity Analysis**:
    - Content-Based Image Retrieval (CBIR)
    - Copy-Move Detection
    - Provenance Analysis
    - TruFor (Forgery Detection)
    - Watermark Removal
- **Robust Architecture**:
    - Docker containerization
    - MongoDB for persistent storage
    - Redis for task queues

## Getting Started

Check out the [Getting Started](development/setup.md) guide to set up the system locally.

## Architecture

Learn about the system's design in the [Architecture Overview](architecture/overview.md).

## API Reference

Explore the API endpoints in the [API Reference](api/reference.md).
