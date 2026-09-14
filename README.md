
<div align="center">
<pre>
███████╗  ██╗       ██╗  ███████╗
██╔════╝  ██║       ██║  ██╔════╝
█████╗    ██║       ██║  ███████╗
██╔══╝    ██║       ██║  ╚════██║
███████╗  ███████╗  ██║  ███████║
╚══════╝  ╚══════╝  ╚═╝  ╚══════╝
Scientific Integrity System
</pre>
</div>

<div align="center">

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Open Source](https://badges.frapsoft.com/os/v1/open-source.svg?v=103)](https://github.com/ellerbrock/open-source-badges/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)

</div>

# ELIES - Scientific Integrity System

**ELIES** is a **FOREVER FREE AND OPEN-SOURCE** system designed to analyze the integrity of scientific data.

Our goal is to democratize access to advanced forensic tools, empowering researchers and integrity officers with robust and transparent tools to ensure the integrity of scientific records.

Currently, the system is focused on **image forensics**, but future versions will extend to text and statistical data analysis.



https://github.com/user-attachments/assets/f23e7ad3-1dbd-46d5-b5c5-5b897fd4a88b


---

## Getting Started

To get ELIES running on your machine, you will need [Docker Compose](https://docs.docker.com/compose/) and [Node.js](https://nodejs.org/).

#### 1. Clone the repository and submodules
```bash
git clone --recurse-submodules git@github.com:researchintegrity/elies.git
cd elies
git submodule update --init --remote # ensure latest submodule versions

```

#### 1.1 Fix .env
```bash
cp .env.example .env
# Edit .env to set the HOST_WORKSPACE_PATH
# >> HOST_WORKSPACE_PATH=<path/to-current-dir>/elies/system_modules/elies-frontend/workspace
```

#### 2. Build the tools
This step could take some while as it will download and compile multiple models from different servers
```bash
docker compose --profile tools build
```

#### 3. Launch the backend
```bash
docker compose up -d
```

#### (Production Alternative) 3. Launch the backend with multiple workers (n=5)
```bash
docker compose -f docker-compose-prod.yml up -d --scale workers=5 
```

#### 4. Launch the frontend
```bash
cd system_modules/elies-frontend
npm install
npm run dev
```

After instalation, visit **[http://localhost:5173](http://localhost:5173)** to see the system in action!

> [!TIP]
> **Need more details?**
> Check our [Technical Overview](docs/TECHNICAL_OVERVIEW.md) for a deep dive into the architecture, manual installation, and API documentation.

### Upgrading from ELIS

The project was previously spelled **ELIS**. If you already run an older checkout:

- **Database**: the default database name is now `elies_system`. To keep your existing data, either set `DATABASE_NAME=elis_system` in your `.env`, or migrate it:
  ```bash
  mongodump --db elis_system --archive=elis.archive
  mongorestore --archive=elis.archive --nsFrom 'elis_system.*' --nsTo 'elies_system.*'
  ```
- **Docker network**: run `docker compose down` *before* pulling, so the old `elis_network` is removed.
- **Frontend path**: the frontend submodule moved to `system_modules/elies-frontend`. Run `git submodule sync && git submodule update --init`, move your existing `workspace/` folder there, and update `HOST_WORKSPACE_PATH` in `.env`.
- **Clone folder**: Docker Compose prefixes volume names (including MongoDB data) with the folder name. If you rename your existing clone folder (e.g. `elis` or `elis-backend`), set `COMPOSE_PROJECT_NAME` to the old folder name to keep using the existing volumes.

---

## Implemented Modules

ELIES integrates multiple specialized modules to detect manipulation.

| Module | Description | Status |
| :--- | :--- | :--- |
| **[PDF Image Extraction](https://github.com/researchintegrity/pdf-image-extraction)** | Extracts images from scientific PDF documents for analysis. | <div align="center">✅</div> |
| **[Panel Extractor](https://github.com/researchintegrity/panel-extractor)** | Uses YOLO models to parse multi-panel figures into individual images. | <div align="center">✅</div> |
| **[Watermark Removal](https://github.com/researchintegrity/watermark-removal)** | Removes "RETRACTED" watermarks from PDF academic articles. | <div align="center">✅</div> |
| **[CBIR Search](https://github.com/researchintegrity/cbir-system)** | Finds similar images across datasets. | <div align="center">✅</div> |
| **[TruFor](https://github.com/researchintegrity/TruFor)** | Detects cheapfakes and image manipulations. | <div align="center">✅</div> |
| **[Copy-Move Detection](https://github.com/researchintegrity/copy-move-detection)** | Identifies duplicated regions within and across images.| <div align="center">✅</div> |
| **[Provenance Analysis](https://github.com/researchintegrity/provenance-analysis)** | Tracks reused and manipulated data across articles and datasets. | <div align="center">✅</div> |


## TODOs

- [ ] Documentation
- [ ] UI/UX Improvement (check the [front-end issues](https://github.com/researchintegrity/elies-frontend/issues))
- [ ] AI-image detection module
- [ ] AI-text detection module
- [ ] OpenAlex API linked to ELIES

Found a bug or want a new feature, please contribute by opening a new issue!

---

## Acknowledgements

The name of this system is a tribute to **Dr. Elisabeth Bik**, a key personality in the field of scientific integrity. Her dedication to uncovering data manipulation has inspired our work and the work of many other researchers worldwide.
Learn more about her work at her blog: [Science Integrity Digest](https://scienceintegritydigest.com/about/)

**Special Thanks:**
*   **[Forensically](https://29a.ch/photo-forensics/#help)**: The Image Analysis module is deeply inspired by this project. Shoutout to [Jonas Wagner](https://github.com/jwagner).
*   **[UNINA Image Processing Research Group](https://www.grip.unina.it/)**: For their work on Dense-Field copy-move detection and [TruFor](https://github.com/grip-unina/TruFor) methods.

---

## License

**ELIES** is open-source software licensed under the **AGPLv3 License**.

> **Note**: Each module integrated into ELIES has its own licensing terms. Some components may have restrictions on commercial use. Please check the `LICENSE` file in each individual module for specific details.

---

<div align="center">
  <i>"No one can predict the positive shift caused by even an ant's step on the path of science and ethics."</i>
</div>

<div align="center">
  <sub>Built with ❤️ for Science</sub>

</div>
