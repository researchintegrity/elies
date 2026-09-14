# Architecture Overview

The ELIES system is built using a microservices-inspired architecture, orchestrated by Docker Compose.

## Core Components

### Web API (FastAPI)
The entry point for the system. Handles HTTP requests, authentication, and delegates tasks to background workers.

### Database (MongoDB)
Stores metadata for users, documents, images, and analysis results.

### Task Queue (Redis & Celery)
Handles asynchronous tasks such as:
- PDF processing
- Image extraction
- Running heavy analysis models (CBIR, TruFor, etc.)

### Worker Nodes
Scalable worker containers that consume tasks from the Redis queue.

### System Modules
Specialized containers/modules for specific analysis tasks:
- **CBIR System**: Content-Based Image Retrieval.
- **Panel Extractor**: Extracts sub-panels from compound figures.
- **TruFor**: Forgery detection model.
- **Provenance Analysis**: Tracks image lineage.
