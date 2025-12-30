# Lore App Architecture

This document orients future AI agents and maintainers so code generation and reviews stay fast and accurate. Update it whenever the module graph or request flow meaningfully changes.

## High-Level Overview
- **FastAPI shell (`app/main.py`)** – Creates the ASGI `app`, mounts static/upload directories, and wires page + API routers. External servers should import `create_application()` to reuse the configured instance.
- **Routes (`app/routes/`)**
  - `pages.py`: Templated HTML routes for the landing page and collection view.
  - `api.py`: Public JSON API for collections, ingestion, chunking, things, and connections. Delegates to domain services and enforces input validation/error handling.
- **Domain layer (`app/domain/`)**
  - `collections.py`: Chroma client setup, collection helpers, and metadata sanitization.
  - `chunks.py`: Persistence for detected chunk sets on disk (`chunks.json`).
  - `chunking/`: Chunk detection logic. `core.py` handles low-level segmentation; `pipeline.py` orchestrates detection + LLM-based enrichment; `orchestrator.py` manages reuse/detection workflows and annotation helpers.
  - `library.py`: File-backed storage for lore entities (`Thing`) and relationships (`Connection`) in `library.json`.
  - `ingestion/`: Pipelines that transform raw text into structured lore. `pipeline.py` uses OpenIP for extraction and the chunking orchestrator; `openai_ingest.py` runs an OpenAI-based extraction and chunking path; `openip_client.py` is the HTTP client wrapper.
- **Schema contracts (`app/schemas.py`)** – Pydantic models shared across routes and domain logic.
- **Upload utilities (`app/upload_store.py`)** – Handles file persistence and best-effort text extraction.
- **Static/templates (`app/static`, `app/templates`)** – Frontend assets and Jinja templates.

## Data Flow Highlights
1. **User uploads or text ingest** → `routes.api` → `ingestion` pipeline → `library` (things/connections) + `chunks` (disk store) → optional Chroma indexing via `collections`.
2. **Chunking UI endpoints** → `chunking.orchestrator.detect_or_reuse_chunks` to reuse cached chunk sets or call the detection pipeline.
3. **Querying** → `routes.api.chunks_query` → Chroma collection with sanitized metadata filters.

## Development Notes for Future Agents
- Maintain docstrings for every outward-facing function, method, and API route. Summaries should clarify purpose, inputs, outputs, and error behavior.
- When adjusting flows, update this document and keep cross-module responsibilities clear (routes delegate to domain; domain stays framework-light).
- Prefer deterministic IDs and sanitized metadata when writing to Chroma or disk files.
- Keep upload handling resilient—validate content, guard against empty input, and surface actionable errors.
- If you introduce new services or change chunking/ingestion parameters, document defaults and rationale here and in the relevant docstrings.
