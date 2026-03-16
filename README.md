# OSINT Missing Persons Canada

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status: Active Development](https://img.shields.io/badge/status-active%20development-brightgreen.svg)]()

An open-source intelligence (OSINT) platform designed to help locate missing children in Canada. The platform ingests real-time case data from the [Missing Children Society of Canada](https://mcsc.ca/), then layers on active investigation capabilities including digital footprint analysis, web mention scanning, and facial recognition cross-matching.

> **This tool is built to cooperate with law enforcement.** All findings are intended to be reported to police — not for vigilante action.

---

## Live Dashboard

**[VIEW LIVE DASHBOARD](https://osint-missing-persons-ca-production.up.railway.app)**

The dashboard shows all active missing children cases across Canada on an interactive map, with case details, photos, and investigation tools.

API documentation (Swagger UI): [Live API Docs](https://osint-missing-persons-ca-production.up.railway.app/docs)

---

## What It Does

### Phase 1 -- Data Foundation (Complete)

Ingests all active missing children cases from the MCSC public ArcGIS API. No scraping needed — structured JSON with coordinates, photos, and case metadata. Background scheduler keeps data in sync hourly.

### Phase 2 -- Digital Footprint Engine (Complete)

Generates plausible usernames from a missing person's name and checks for account existence across 15 platforms with reliable detection. Simultaneously scans Google News, Reddit, and DuckDuckGo for web mentions. All leads are scored by confidence.

### Phase 3 -- Facial Search Engine (Complete)

Extracts faces from case photos, computes 128-dimensional face encodings using `face_recognition` (dlib), and compares across all cases to find potential matches. Supports image upload search and pluggable reverse image search providers (PimEyes, TinEye, Google Vision).

### Upcoming

| Phase | Name | Status |
|-------|------|--------|
| 4 | Trafficking Indicator Monitor | Planned |
| 5 | Social Network Analysis | Planned |
| 6 | Abductor Tracking | Planned |
| 7 | Intelligence Hub | Planned |

---

## Quick Start

### Prerequisites

- Python 3.11+
- [CMake](https://cmake.org/download/) (required for building `dlib`)
- Git

### Setup

```bash
git clone https://github.com/consistentlearningguy/osint-missing-persons-ca.git
cd osint-missing-persons-ca

python -m venv venv

# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

### Initial Data Sync

```bash
python -m scripts.initial_sync
```

Fetches all active cases and photos from the MCSC API into `data/db.sqlite`.

### Index Faces

```bash
python -m scripts.index_faces --match
```

Options: `--force` (re-index all), `--match` (run cross-case matching), `--case 8037` (single case), `--threshold 0.5` (custom threshold).

### Start the Server

```bash
python -m backend.main
```

Open **http://127.0.0.1:8000** in your browser.

For local development, set `HOST=127.0.0.1` and `DEBUG=true` in your `.env` file.

---

## Project Structure

```
osint-missing-persons-ca/
├── backend/
│   ├── main.py                         # FastAPI app entrypoint
│   ├── core/
│   │   ├── config.py                   # Settings from environment variables
│   │   ├── database.py                 # SQLAlchemy engine + session
│   │   └── scheduler.py               # Background sync scheduler
│   ├── ingestion/
│   │   └── mcsc_client.py              # MCSC ArcGIS API client + photo downloader
│   ├── analysis/
│   │   ├── username_search.py          # Username enumeration (15 platforms)
│   │   ├── web_mentions.py             # Web mention scanner (News, Reddit, DDG)
│   │   ├── lead_scoring.py             # Confidence scoring engine
│   │   ├── investigate.py              # Investigation orchestrator
│   │   ├── face_engine.py              # Face detection, encoding, matching
│   │   └── reverse_image_search.py     # Pluggable reverse image search
│   ├── api/
│   │   ├── cases.py                    # Case CRUD + stats + GeoJSON
│   │   ├── sync.py                     # Manual sync trigger + history
│   │   ├── investigations.py           # Investigation lifecycle + leads
│   │   └── faces.py                    # Face index/match/search/review
│   ├── models/
│   │   ├── case.py                     # MissingCase, CasePhoto, SyncLog
│   │   ├── investigation.py            # Investigation, Lead
│   │   └── face.py                     # FaceEncoding, FaceMatch
│   ├── templates/                      # Jinja2 HTML templates
│   └── static/                         # CSS + JavaScript
├── scripts/
│   ├── initial_sync.py                 # One-time data sync
│   └── index_faces.py                  # Face indexing CLI
├── data/                               # Runtime data (gitignored)
├── .env.example                        # Environment variable template
├── Procfile                            # Railway deployment
├── railway.toml                        # Railway build config
└── requirements.txt
```

---

## API Reference

### Cases

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/cases` | List cases (filter by province, status, name, age) |
| `GET` | `/api/cases/stats` | Aggregate statistics |
| `GET` | `/api/cases/geojson` | GeoJSON for map rendering |
| `GET` | `/api/cases/{objectid}` | Single case with photos |

### Data Sync

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/sync` | Trigger manual sync from MCSC |
| `GET` | `/api/sync/history` | Sync log entries |

### Investigations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/investigations/{case_objectid}` | Start OSINT investigation |
| `GET` | `/api/investigations/{case_objectid}` | Investigation status + history |
| `GET` | `/api/investigations/{case_objectid}/leads` | Leads (filter by type, confidence, reviewed) |
| `PATCH` | `/api/investigations/leads/{lead_id}` | Review a lead |

### Facial Recognition

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/faces/index` | Index faces from case photos |
| `GET` | `/api/faces/stats` | Face index statistics |
| `GET` | `/api/faces/case/{case_objectid}` | Face data for a case |
| `POST` | `/api/faces/match` | Run cross-case face matching |
| `GET` | `/api/faces/matches` | List face matches |
| `POST` | `/api/faces/search` | Upload image to search all faces |
| `PATCH` | `/api/faces/matches/{match_id}` | Review a face match |

Full interactive docs at `/docs` (Swagger UI) when running.

---

## Configuration

All settings via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///data/db.sqlite` | Database connection string |
| `SYNC_INTERVAL_MINUTES` | `60` | Background sync frequency |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `DEBUG` | `false` | Enable debug mode + hot reload |
| `FACE_DETECTION_MODEL` | `hog` | `hog` (fast/CPU) or `cnn` (accurate/GPU) |
| `FACE_MATCH_THRESHOLD` | `0.55` | Face distance threshold (lower = stricter) |
| `PIMEYES_API_KEY` | — | Optional: PimEyes reverse image search |
| `GOOGLE_VISION_API_KEY` | — | Optional: Google Vision |
| `TINEYE_API_KEY` | — | Optional: TinEye |

---

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy, SQLite
- **Frontend:** Jinja2, Tailwind CSS, Leaflet.js
- **Face Recognition:** face_recognition (dlib), Pillow, NumPy
- **HTTP Client:** httpx (async)
- **Scheduler:** APScheduler
- **Deployment:** Railway (Nixpacks)

---

## Data Source

All case data comes from the **Missing Children Society of Canada (MCSC)** public ArcGIS FeatureServer API. This is publicly accessible structured data provided by a registered Canadian non-profit. No scraping, no authentication, no terms of service violations.

---

## Legal and Ethical Use

- This platform **assists law enforcement** — it does not replace it. Report all leads to the police authority listed on each case.
- **Do not** use this tool for vigilante action, harassment, or any purpose that could endanger a missing person's safety.
- Username enumeration and web scanning use only publicly accessible endpoints with no authentication bypass.
- Face matching is performed locally. No biometric data leaves your machine unless you configure optional reverse image search API keys.

---

## Contributing

This project is in active development. If you are a law enforcement professional, data scientist, or developer interested in helping locate missing children, please open an issue or start a discussion.

---

## License

[MIT](LICENSE)
