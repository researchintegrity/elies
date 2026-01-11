# Migrating `system_modules` to Git Submodules

This guide explains how to manually convert the existing folders in `system_modules/` into properly tracked Git Submodules. This ensures that anyone cloning the `elis-system` backend will also get automatic links to all the component repositories.

## Prerequisites

1.  **Backup**: Ensure you have pushed any pending changes in your sub-repositories (`system_modules/*`).
2.  **Clean State**: It is safer to start with empty folders or move the existing ones out of the way, as `git submodule add` expects to clone a fresh repository.

## Step 1: Update .gitignore

First, we need to stop ignoring the `system_modules` directory so git can track the submodule references.

Open `.gitignore` and **remove** (or comment out) these lines:

```gitignore
# system modules
system_modules/

# ... and any specific exclusions like:
!system_modules/front-end-platform/
!system_modules/cbir-system/
```

## Step 2: Remove Existing Untracked Folders

Git refuses to add a submodule if a directory with that name already exists and is not an empty git repo.

**Option A (Safest): Move them aside**
```bash
mkdir ../elis_backup
mv system_modules/* ../elis_backup/
# Now system_modules/ is empty
```

**Option B (If you are sure they are up-to-date): Delete them**
```bash
rm -rf system_modules/*
```

## Step 3: Add the Submodules

Run the following commands from the root of the `elis-system` repository.

```bash
# Frontend
git submodule add -f https://github.com/researchintegrity/elis-frontend.git system_modules/elis-frontend

# Core Analysis Modules
git submodule add -f https://github.com/researchintegrity/pdf-image-extraction.git system_modules/pdf-image-extraction
git submodule add -f https://github.com/researchintegrity/panel-extractor.git system_modules/panel-extractor
git submodule add -f https://github.com/researchintegrity/TruFor.git system_modules/TruFor
git submodule add -f https://github.com/researchintegrity/cbir-system.git system_modules/cbir-system

# Specialized Modules
git submodule add -f https://github.com/researchintegrity/copy-move-detection.git system_modules/copy-move-detection
git submodule add -f https://github.com/researchintegrity/copy-move-detection-keypoint.git system_modules/copy-move-detection-keypoint
git submodule add -f https://github.com/researchintegrity/provenance-analysis.git system_modules/provenance-analysis
git submodule add -f https://github.com/researchintegrity/watermark-removal.git system_modules/watermark-removal 
```

> **Note**: I did not find git repositories for `manual-analysis` or `upm`. If you have repos for them, add them similarly.

## Step 4: Verify and Commit

Check that a `.gitmodules` file was created and the folders are staged.

```bash
git status
# You should see:
#   new file:   .gitmodules
#   new file:   system_modules/TruFor
#   ...
```

Commit the changes:

```bash
git add .
git commit -m "chore: convert system_modules to git submodules"
```

## Step 5: How Users Will Clone Now

Update your `README.md` to tell users to use `--recursive`:

```bash
git clone --recursive https://github.com/researchintegrity/elis-system.git
```

Or if they already cloned:
```bash
git submodule update --init --recursive
```
