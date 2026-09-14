# Development Setup

## Prerequisites
- Docker & Docker Compose
- Python 3.10+

## Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd elies-backend
   ```

2. **Environment Variables**:
   Copy `.env.example` to `.env` and configure it.
   ```bash
   cp .env.example .env
   ```

3. **Start Services**:
   ```bash
   docker-compose up -d --build
   ```

4. **Access the Application**:
   The API will be available at `http://localhost:8000`.
