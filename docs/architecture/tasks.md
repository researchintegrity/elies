# Asynchronous Tasks

Tasks are defined in `app/tasks/` and executed by Celery workers.

## Workflow

1. **Upload**: User uploads a document.
2. **Trigger**: `process_document` task is queued.
3. **Extraction**: Images are extracted from the PDF.
4. **Analysis**: Depending on configuration, analysis tasks are chained:
    - `panel_extraction`
    - `cbir_search`
    - `copy_move_detection`
