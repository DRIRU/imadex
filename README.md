# imadex

A local-first personal image library. `imadex` runs a small dashboard on your PC,
keeps the original photos in **Google Drive** (or on local disk), and builds a
private **image embedding index** on this machine so you can find pictures by
describing them in plain English.

It recursively indexes folders, filters by image format, streams previews and
originals without copying them to disk, and tracks favorites, tags, and exact
duplicate copies. Nothing is sent to an external inference API — only Google
Drive access needs the network.

> The web UI and this project are branded **imadex**. Tunnel mode still signs in
> with the username `frame` for backward compatibility.

---

## Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Run with Docker](#run-with-docker)
- [Project layout](#project-layout)
- [How the embedding pipeline works](#how-the-embedding-pipeline-works)
- [Connect Google Drive](#connect-google-drive)
- [Using the library](#using-the-library)
- [People albums and face recognition](#people-albums-and-optional-face-detection)
- [Phone access through Cloudflare Tunnel](#phone-access-through-cloudflare-tunnel)
- [Configuration reference](#configuration-reference)
- [HTTP API reference](#http-api-reference)
- [Data, privacy, and limits](#data-privacy-and-limits)
- [Testing and verification](#testing-and-verification)
- [Troubleshooting](#troubleshooting)
- [Tech stack](#tech-stack)

---

## Features

- **Natural-language visual search.** Describe an image ("a beach at sunset")
  and rank your collection by embedding cosine similarity (SigLIP 2 by default).
- **Google Drive source.** Read-only OAuth 2.0 (PKCE); originals stay in Drive
  and are streamed on demand, never written to disk.
- **Local-folder source.** Index any folder on the PC; small JPEG thumbnails are
  cached under `data/thumbnails/`.
- **Recursive, resumable scans.** New or changed files are queued automatically;
  each image is checkpointed independently and failures are reported per file.
- **Exact duplicate detection** using content checksums (SHA-256 locally, MD5
  from Drive).
- **Favorites, tags, folder/format filters, and sorting.**
- **Text search** over filenames, source paths, tags, and camera metadata.
- **People albums** with manual labels and optional local YuNet face detection;
  optional ArcFace recognition auto-groups high-confidence matches and asks about
  unclear ones.
- **Embedded or external Qdrant** vector store (`QDRANT_URL`), with optional CPU
  and GPU server profiles in Docker Compose.
- **Docker / Docker Compose** packaging, plus optional CUDA acceleration for the
  embedding and recognition models (CPU fallback).
- **Local-only server** bound to `127.0.0.1`, with strict Host/Origin checks.
- **Secure remote access** through Cloudflare Tunnel with HTTP Basic auth.

---

## Requirements

- **Python 3.12 or newer.**
- Google Drive API access only if you use the Drive source (see
  [Connect Google Drive](#connect-google-drive)).
- Roughly **600 MB** of model weights, downloaded on first use and cached under
  `data/models/`.

Python dependencies ([`requirements.txt`](requirements.txt)):

| Package                  | Version   | Purpose                                     |
| ------------------------ | --------- | ------------------------------------------- |
| `Pillow`                  | 12.2.0      | Decoding, EXIF orientation, thumbnails            |
| `fastembed`               | 0.8.1       | ONNX inference for SigLIP 2 / CLIP image/text      |
| `qdrant-client`           | 1.19.1      | Embedded vector store (local mode)                |
| `opencv-python-headless`  | 4.11.0.86   | YuNet face detection + face alignment             |
| `onnxruntime`             | 1.30.0      | Runs SigLIP 2/CLIP and ArcFace (CPU; swap for `onnxruntime-gpu` for CUDA) |

---

## Quick start

```powershell
cd C:\Users\Jezt\Documents\sangeeth\imadex
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:8765>. Alternatively run `start.ps1`, which creates the
virtual environment on first launch, installs dependencies, offers to download the
ArcFace model when it is missing, and then starts the server. Extra arguments are
forwarded to `app.py`, for example `.\start.ps1 --port 8766`.

- Stop with `Ctrl+C` in the terminal.
- Use a different port with `.\.venv\Scripts\python.exe app.py --port 8766`.
- **Always use the project virtual environment** — it holds the embedding
  dependencies.

---

## Run with Docker

A [`Dockerfile`](Dockerfile) and [`docker-compose.yml`](docker-compose.yml) are
included for a containerized deployment. Docker is optional; the venv workflow
above is the primary path.

```sh
docker compose up --build
```

Open <http://127.0.0.1:8765>. The compose file publishes the port as
`127.0.0.1:8765` so the service stays loopback-only on the host, matching the
app's local-only design. The container itself binds `0.0.0.0` (via
`--host 0.0.0.0`); the app still rejects any `Host`/`Origin` that is not loopback
or your configured public origin.

- **State is persisted** through the `./data:/app/data` volume, so the SQLite
  catalog, Qdrant vectors, thumbnails, model weights, and OAuth files survive
  rebuilds. Put `data/google-client.json` on the host before connecting Drive.
- **First run downloads ~1.5 GB** of SigLIP 2 weights (~0.6 GB for CLIP) plus a
  ~233 KB YuNet model on
  first face detection) into `./data/models/`. The healthcheck reports healthy
  once the HTTP server is answering, which can happen before weights finish.
- **Stop** with `Ctrl+C` or `docker compose down`; **rebuild** after code
  changes with `docker compose up --build`. Run a single service against a given
  `data/` directory.
- **Tunnel mode:** copy [`.env.example`](.env.example) to `.env`, set
  `FRAME_PUBLIC_ORIGIN` and `FRAME_PASSWORD`, then restart. Point your
  Cloudflare Tunnel at `http://127.0.0.1:8765` on the host; see
  [Phone access through Cloudflare Tunnel](#phone-access-through-cloudflare-tunnel).
- **Google Drive sign-in** must be completed on the PC at
  `http://127.0.0.1:8765`, exactly as in the native workflow.
- Files not needed at runtime (`data/`, `.git/`, caches, logs, secrets) are kept
  out of the image by [`.dockerignore`](.dockerignore).

To run the test suite inside the image:

```sh
docker compose run --rm --entrypoint python imadex -m unittest discover -s tests -v
```

---

## Project layout

```
imadex/
├─ app.py                  HTTP server, SQLite catalog, scanning, REST + static serving
├─ drive.py                Read-only Google Drive client (OAuth 2.0 with PKCE)
├─ semantic.py             SigLIP 2/CLIP image-text embeddings + Qdrant vector search
├─ people.py               Manual People albums: labels, covers, merge/delete
├─ detection.py            Optional local YuNet face-region detection
├─ recognition.py          Optional ArcFace face recognition and auto-grouping
├─ download_arcface.py     Fetch the ArcFace model (accepts its license)
├─ backup_catalog.py       Consistent SQLite-only catalog backup
├─ verify_embeddings.py    Real image-model + Qdrant smoke test (temp catalog)
├─ verify_detection.py     Real YuNet face-detection smoke test
├─ verify_recognition.py   Real ArcFace face-embedding smoke test
├─ requirements.txt        Pinned Python dependencies
├─ Dockerfile              Container image (python:3.12-slim)
├─ docker-compose.yml      Single-service compose definition
├─ .dockerignore           Keeps state, secrets, and caches out of the image
├─ .env.example            Template for tunnel-mode environment variables
├─ start.ps1               Create venv (first run), install deps, run the server
├─ start-tunnel-access.ps1 Start in authenticated tunnel mode with a password
├─ server.log              Server output (gitignored)
├─ licenses/               Third-party license texts (e.g. YuNet)
├─ web/                    Static front end (no build step)
│  ├─ index.html           Dashboard shell
│  ├─ app.js               Client logic, polling, viewer, search
│  ├─ people.js            People albums and detection review UI
│  ├─ style.css            Desktop styling
│  ├─ mobile.css           Responsive / phone layout
│  ├─ semantic.css         Visual-search panel styling
│  ├─ people.css           People and detection styling
│  └─ favicon.svg
├─ tests/
│  ├─ test_catalog.py      Catalog, scanning, API, duplicates, security
│  ├─ test_semantic.py     Embedding lifecycle with deterministic model stubs
│  ├─ test_people.py       People labels, filters, and detection state
│  └─ test_recognition.py  ArcFace grouping, suggestions, and API auth
└─ data/                   Runtime state — created on first launch (gitignored)
   ├─ catalog.sqlite3      Metadata source of truth (WAL)
   ├─ vectors/             Qdrant local collection
   ├─ models/              Cached SigLIP 2/CLIP, YuNet, and ArcFace weights
   ├─ thumbnails/          Cached JPEG thumbnails for local sources
   ├─ google-client.json   Your Desktop OAuth client (you provide)
   └─ google-token.json    OAuth tokens (written after sign-in)
```

The web server binds **only to `127.0.0.1`** by default (`--host 0.0.0.0` is used
only inside the container). `data/` and the OAuth files are never served by the
web server.

---

## How the embedding pipeline works

**Vector database.** Qdrant in *local mode*, persisted under `data/vectors/`. It
runs in-process — there is no Docker container, cloud vector account, or
separate server to start. SQLite (`data/catalog.sqlite3`) remains the source of
truth for metadata, tags, favorites, and embedding job state.

**Models.** [FastEmbed](https://qdrant.github.io/fastembed/) 0.8.1 runs a paired
image/text dual encoder. The default is **SigLIP 2**
(`google/siglip2-base-patch16-224`, 768-dimensional, Apache-2.0, multilingual);
set `IMAGE_INDEX_MODEL=clip` to use the older
`Qdrant/clip-ViT-B-32-vision` / `Qdrant/clip-ViT-B-32-text` pair
(512-dimensional). Both towers emit vectors in the same space; vectors are
normalized and ranked by cosine similarity. Each model writes to its own Qdrant
collection, so switching re-indexes once without mixing dimensions. Inference runs
via ONNX Runtime (CPU by default; see GPU acceleration below).

See FastEmbed's [image support](https://qdrant.github.io/fastembed/examples/Image_Embedding/)
and [supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/).

**Pipeline:**

1. Scan a selected folder (and subfolders) to collect file IDs, metadata, and
   content checksums.
2. Queue new or changed images. Drive originals are fetched into memory through
   the authenticated API, verified against their checksum when available, EXIF
   re-oriented, and converted to RGB. Local images are read from their paths.
3. Generate an image vector locally. Only vectors and processing state are
   persisted; Drive originals are not written to disk. Identical bytes reuse an
   existing embedding.
4. Encode the search description with the paired text model and ask Qdrant
   for the closest image vectors. Folder, format, favorites, and duplicate
   filters still apply; removed/outdated vectors are excluded.

Each embedding carries a fingerprint that includes the model/preprocessing
version, so changing content or the model invalidates stale vectors. The
**Visual search** panel shows ready/total counts, progress, and per-file
failures; **Index images** backfills and retries failures. Existing vectors
survive restarts.

Model weights download on first use into `data/models/`. Subsequent inference is
fully offline except for Google Drive access.

### GPU acceleration (optional)

By default inference runs on the CPU via ONNX Runtime using up to four threads.
You can optionally use an NVIDIA GPU for the SigLIP 2/CLIP and ArcFace models:

1. Uninstall the CPU package and install the GPU build (they conflict):
   `pip uninstall onnxruntime` then `pip install onnxruntime-gpu`, plus a matching
   CUDA/cuDNN runtime on Windows.
2. Set `IMAGE_INDEX_PROVIDER` (see the [configuration reference](#configuration-reference)):
   - `auto` (default) — use CUDA when the installed onnxruntime offers it, otherwise CPU;
   - `cpu` — always CPU;
   - `cuda` — require CUDA and fail clearly if the GPU build is missing.

The current provider is shown in the **Visual search** and **People** panels and
returned by `/api/embeddings` and `/api/recognition` (`provider`). Only inference
is accelerated: Qdrant local mode and the pip OpenCV build stay CPU-only, and
Drive download/decode can still dominate indexing time.

### External Qdrant server (optional)

By default, vectors live in embedded Qdrant local mode under `data/vectors/`. For
larger libraries, or to run the store separately, point the app at a Qdrant server:

```powershell
$env:QDRANT_URL = 'http://127.0.0.1:6333'
# optional: $env:QDRANT_API_KEY = '...'
.\.venv\Scripts\python.exe app.py
```

With Docker Compose, the repository includes optional server profiles:

- CPU: `docker compose --profile qdrant up --build`, with `QDRANT_URL=http://qdrant:6333` in `.env`.
- GPU: `docker compose --profile qdrant-gpu up --build`, with `QDRANT_URL=http://qdrant-gpu:6333` in `.env`.

Notes:

- `QDRANT_COLLECTION` overrides the collection name (default `images_clip_b32_v1`);
  `QDRANT_TIMEOUT` sets the client timeout in seconds (default `60`).
- Embedded and server modes use separate vector stores. After switching, run
  **Index images** once to populate the new store. The SQLite catalog (source of
  truth for metadata) is unaffected.
- `/api/embeddings` reports the active backend in its `database` field.
- Only one app process should write to a given store.

#### Can Qdrant be GPU accelerated?

Yes, but with important limits (Qdrant **v1.13+**):

- GPU is used for **indexing only** (building the HNSW index), **not search**. It
  is enabled with `QDRANT__GPU__INDEXING=1`; the `qdrant-gpu` compose service sets
  this for you.
- GPU support ships only as dedicated Docker images for **Linux x86_64**: NVIDIA
  `qdrant/qdrant:gpu-nvidia-latest`, AMD `qdrant/qdrant:gpu-amd-latest`. **Windows,
  macOS, and ARM are not supported**, and NVIDIA requires the
  [`nvidia-container-toolkit`](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
- Each GPU processes up to **16 GB** of vector data per indexing iteration.
- Qdrant's GPU path uses Vulkan. Qdrant local mode and the pip OpenCV build are
  never GPU-accelerated.
- At this project's personal-library scale, embedded or CPU-server Qdrant is
  usually plenty; GPU mainly helps when building very large indexes.

See Qdrant's [Running with GPU](https://qdrant.tech/documentation/ops-configuration/running-with-gpu/)
and [Installation](https://qdrant.tech/documentation/installation/) docs.

---

## Connect Google Drive

The dashboard needs its own OAuth client. A Google Drive connection elsewhere
does **not** provide credentials to this standalone application.

1. In the [Google Cloud Console](https://console.cloud.google.com/), create or
   select a project.
2. Enable the **Google Drive API** under *APIs & Services*.
3. Configure the Google Auth Platform branding and audience. For a personal
   account, choose **External** and add your own email as a test user while the
   app is in *Testing*.
4. Under *Clients*, create an OAuth client of type **Desktop app** and download
   its JSON configuration.
5. Save that file as `data/google-client.json`. The `data` folder is created on
   first launch. **Do not commit it or paste it into chat.**
6. Click **Connect Google Drive** and finish sign-in in your browser.
7. Click **Add Drive folder**, then paste the folder's Google Drive URL or ID.
   The app indexes that folder and all ordinary subfolders.

Authorization uses OAuth with PKCE, a random `state`, and a loopback callback.
The app requests `drive.readonly` because indexing a folder tree and reading
private image bytes requires access beyond individually opened files. This scope
grants read access across Drive, but the app indexes only the folders you add
and never writes to your Drive. Google may show an unverified-app screen for a
personal test application.

> Google's External/Testing OAuth refresh tokens commonly **expire after seven
> days**. Reconnect if Google rejects a refresh. Broader distribution or hosting
> needs a separate auth/deployment design and may require OAuth verification. See
> Google's [native app OAuth guide](https://developers.google.com/identity/protocols/oauth2/native-app)
> and [Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

---

## Using the library

- Select a folder and click **Scan folder** to refresh its index. Scans run in
  the background, one at a time, and are manual (no scheduled sync).
- Open a card for a larger preview, details, favorites, tags, and a link to the
  original. Arrow keys navigate loaded images; `Esc` closes; `/` focuses search.
- **Visual search** (default) ranks embedded images by a description such as
  "a beach at sunset". The result line reports any candidates still awaiting
  indexing.
- **Names & tags** matches all typed words across filenames, source paths, local
  tags, and camera metadata.
- **Duplicates** are exact checksum matches; visually similar images are not
  detected. The statistic counts extra copies, while the view lists every file
  in a duplicate group.
- Missing or trashed files are hidden after a successful rescan. Interrupted or
  failed scans never mark unseen files missing. Tags and favorites survive
  rescans.
- Drive shortcuts are not traversed. Add ordinary, non-overlapping folder roots.

Scores shown in visual search are **relative similarities, not confidence
percentages**; unrelated results may still appear lower in the ranking. It works
best with short English descriptions of scenes, objects, and colors. This is not
OCR, face identification, or automatic tagging.

---

## Phone access through Cloudflare Tunnel

The mobile layout includes bottom navigation, a folder selector, a two-column
gallery, 44px touch targets, safe-area spacing, and a full-screen viewer. Swipe
across a preview to navigate. Previews load small thumbnails; tap **Load
full-resolution image** only when needed. Status polling pauses in background
tabs and slows to every 15 seconds when no scan is running.

1. Connect Google Drive **on the PC** first via `http://127.0.0.1:8765`. The
   Desktop OAuth callback is a loopback address and cannot complete on a phone.
2. Install `cloudflared` using [Cloudflare's instructions](https://developers.cloudflare.com/tunnel/get-started/),
   then create a tunnel. For a stable URL, configure a named tunnel public
   hostname (e.g. `photos.your-domain.com`) with service URL
   `http://127.0.0.1:8765`. Leave the HTTP Host Header override unset and keep
   the tunnel token private.
3. Stop the existing server, then start authenticated mode with your actual
   hostname:

   ```powershell
   .\start-tunnel-access.ps1 -PublicOrigin 'https://photos.your-domain.com'
   ```

   The script prompts for a password (minimum 16 characters) and does not write
   it to disk or history. Sign in as **frame** with that password. Local browser
   access also requires the password while tunnel mode is enabled.
4. Start your Cloudflare connector and open the HTTPS URL on your phone. The PC
   and connector must remain running. The tunnel forwards to loopback; no
   inbound firewall port or `0.0.0.0` bind is required.

The app validates `Host` and `Origin` and requires HTTP Basic auth on every
route, including thumbnails and originals. Basic auth relies on the tunnel's
HTTPS connection. Never publish the app in ordinary local mode by rewriting the
tunnel's Host header to `localhost`. For a persistent deployment you can also
restrict the hostname to your email with Cloudflare Access. Do not add a
Cloudflare cache rule that overrides the app's private/no-store cache policy.

For temporary testing, [Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)
generate a random HTTPS URL; stop any unprotected server first and restart
`imadex` with that exact origin. A new URL requires restarting with the new
origin. Quick Tunnels are for testing only.

No tunnel is created or published automatically by this repository, and Google
credentials never leave the PC.

---

## Configuration reference

| Setting | Default | Purpose |
| ------- | ------- | ------- |
| `--host` | `127.0.0.1` | Bind address. Set `0.0.0.0` only inside a container. |
| `--port` | `8765` | HTTP listen port. |
| `--public-origin` | – | Exact HTTPS origin allowed for tunnel mode. |
| `IMAGE_INDEX_DATA` | `./data` | Override the runtime data directory. |
| `IMAGE_INDEX_HOST` | `127.0.0.1` | Env equivalent of `--host`. |
| `IMAGE_INDEX_PROVIDER` | `auto` | ONNX provider: `auto`, `cpu`, or `cuda`. |
| `IMAGE_INDEX_MODEL` | `siglip2` | Image/text encoder: `siglip2` or `clip`. |
| `QDRANT_URL` | – | External Qdrant URL; empty uses embedded local mode. |
| `QDRANT_API_KEY` | – | API key for the external Qdrant server, if any. |
| `QDRANT_COLLECTION` | `images_clip_b32_v1` | Collection name. |
| `QDRANT_TIMEOUT` | `60` | Qdrant client timeout in seconds (server mode). |
| `FRAME_PUBLIC_ORIGIN` | – | Env equivalent of `--public-origin`. |
| `FRAME_PASSWORD` | – | Tunnel-mode password (≥16 chars). Required with a public origin. |

Tunnel mode is rejected unless `--public-origin` is a clean `https://host`
origin and `FRAME_PASSWORD` is at least 16 characters.

---

## HTTP API reference

All endpoints are served on the loopback interface and require an allowed
`Host`/`Origin`. In tunnel mode, every route additionally requires HTTP Basic
auth (`frame:<password>`). JSON responses set `Cache-Control: no-store`.

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/` and `/app.js`, `/style.css`, `/mobile.css`, `/semantic.css`, `/favicon.svg` | Static dashboard assets. |
| `GET` | `/api/status` | Current scan status (running, processed, errors, message). |
| `GET` | `/api/library` | Folders, aggregate stats, available formats. |
| `GET` | `/api/images` | Paged image list. Query: `q`, `mode=semantic\|text`, `folder`, `format`, `view=favorites\|duplicates`, `sort`, `offset`. |
| `GET` | `/api/drive` | Drive configuration/connection state. |
| `GET` | `/api/embeddings` | Visual-index progress and per-model status. |
| `GET` | `/image/{id}` | Stream the original (Drive or local). |
| `GET` | `/thumb/{id}` | Stream a preview thumbnail. |
| `GET` | `/oauth/callback` | OAuth redirect target (PKCE). |
| `POST` | `/api/drive/connect` | Begin Google sign-in; returns the auth URL. PC-only. |
| `POST` | `/api/folders` | Add a folder. Body: `{source:'drive'\|'local', path}`. |
| `POST` | `/api/scan` | Start a background scan. Body: `{folder_id}`. |
| `POST` | `/api/image` | Update `favorite` and/or `tags`. Body: `{id, favorite?, tags?}`. |
| `POST` | `/api/embeddings` | Queue indexing / retry failed embeddings. |

`POST` requests must send `Content-Type: application/json` and a body ≤ 16 KB.

---

## Data, privacy, and limits

Where things live:

- **Google Drive:** original images and folder organization.
- **This PC:** SQLite metadata catalog, Qdrant vectors (`data/vectors/`), model
  weights (`data/models/`), thumbnails, favorites, tags, and OAuth config/
  tokens. Protect `data/` with your Windows account permissions.
- **Browser:** image/thumbnail responses may be cached for five minutes. The
  server streams Drive previews and originals without writing them to disk.
- **Local-folder sources:** originals are untouched; JPEG thumbnails are written
  under `data/thumbnails/`.

Known limits:

- Originals over **64 MB**, and formats Pillow cannot decode, are reported as
  failed rather than silently skipped. Animated/multipage images use the first
  frame.
- Embedding uses the model's resize/crop preprocessing, so tiny details and text can be
  missed.
- Qdrant local mode runs vector search in-process and is intended for a personal
  collection; very large libraries should move to a dedicated Qdrant server.
- Run only **one** app process against a given `data/` directory. Stop the app
  before backing up `catalog.sqlite3` and `data/vectors/` together.

To disconnect Drive: stop the app, delete `data/google-token.json`, and revoke
the app in your Google account's third-party connections if desired.

---

## Testing and verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe verify_embeddings.py
.\.venv\Scripts\python.exe verify_recognition.py   # requires the ArcFace model
```

Unit/integration tests use temporary images, a real local Qdrant store, and
deterministic model substitutes for lifecycle tests. `verify_embeddings.py`
separately runs **actual** image and text inference for the selected model, verifies
color-description ranking, and checks incremental resume against a temporary
catalog and the shared model cache. `verify_recognition.py` runs real YuNet +
ArcFace inference on a downloaded fixture. Drive downloads are simulated in
tests; live Google OAuth, folder access, and private image streaming require your
own credentials and sign-in.

---

## Troubleshooting

- **"Embedding service is not started."** Run through `start.ps1` or the venv
  Python; the embedding dependencies are required.
- **First visual search is slow.** The SigLIP 2 weights (~1.5 GB; ~0.6 GB for
  CLIP) download on first
  use into `data/models/`. Later runs are offline.
- **"Could not load the visual model."** Check the internet connection on first use and
  confirm `data/models/` is writable, then retry.
- **Google refresh rejected / sign-in fails.** Testing-mode refresh tokens
  expire after ~7 days; reconnect from the PC dashboard.
- **"Add your Google Desktop OAuth client file."** Save the Desktop-app client
  JSON as `data/google-client.json`.
- **A scan marks nothing missing.** That is intentional: only successful scans
  hide unseen files, so interrupted work is never destructive.
- **Port already in use.** Choose another port with `--port`.

---

## Tech stack

Python 3.12 · [Pillow](https://python-pillow.org/) ·
[FastEmbed](https://github.com/qdrant/fastembed) (ONNX Runtime; SigLIP 2 default, CLIP optional) ·
[Qdrant](https://qdrant.tech/) (embedded local or external server) · SQLite · OpenCV (YuNet face
detection and alignment) · [InsightFace](https://github.com/deepinsight/insightface)
ArcFace (face recognition) · vanilla HTML/CSS/JS front end · Google Drive API
(OAuth 2.0 + PKCE) · Cloudflare Tunnel for remote access · Docker / Docker
Compose (optional).


## People albums and optional face detection

Open **People** to create a person, then open a photo and use **Add person**.
You can attach several names to a photo. **Select photos** enables manual batch
selection (up to 100 photos); **Label selected photos** assigns a name to that
exact selection. Selection remains available after a failed request.

Open a People album to combine it with folder, format, favorites, or visual/text
search filters. **Manage person** supports rename, merge, and deletion; merges
and deletions show affected counts first. Open a labeled photo inside the album
to choose **Use as album cover**. The cover falls back to another current photo
if the chosen photo becomes unavailable or its label is removed. Different
people can have the same name; the picker includes record IDs to distinguish them.

Labels are manual. New photos have no names. Labels belong to a catalog image ID
and content revision. Changed content hides old labels from albums until reviewed
in the photo viewer; missing photos are hidden. Drive renames retain labels when
the file ID stays the same. Local renames currently create a new catalog entry,
so labels must be reassigned. Labels are not copied to duplicate files.

In **People → Face detection assistance**, enable optional local detection.
Use **Find face regions** in a photo, or queue the library from People. A single
background worker processes persisted jobs and resumes on restart when enabled.
The review view shows photos with detected regions and no current manual labels;
it does not determine whether a partially labeled photo has everyone accounted for.
**Ignore these detections** dismisses a photo's regions. **Clear detection results**
clears detection jobs/results while keeping labels. Disabling detection stops new
work and hides regions; already-running inference may finish but will not publish
results after disable/clear. Retry failed jobs with the library queue button.

Detection uses OpenCV headless 4.11.0.86 and the
[YuNet 2023mar model](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet),
licensed under MIT (copy in `licenses/YuNet-LICENSE.txt`). Its 232,589-byte model
is downloaded on first detection and checked against SHA-256
`8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
CPU processing uses an EXIF-normalized RGB image, converts it to BGR, and limits
its longest side to 1280 pixels. Small, obscured, or side-on faces can be missed;
false detections can occur. Detection stores normalized rectangles and confidence,
never identity embeddings or automatically inferred names. Manual labels work
without detection. The existing 64 MB input limit applies, and Drive pixels are
read into memory without saving originals. Detection previews use gallery thumbnails.

New SQLite tables in `data/catalog.sqlite3`: `people`, `image_people`,
`detection_settings`, `detections`, and `project_schema`. Migrations are additive
and idempotent. Both new schema components start at version 1. Names, labels,
detection boxes, job errors, and settings remain on the PC. All API routes use
the same configured authentication and origin checks as the gallery.

API additions:

- `GET /api/people?q=&offset=` returns up to 60 people, counts, covers, and edit versions.
- `GET /api/images/{id}/people` returns labels, stale flags, and current content revision.
- `POST /api/people` takes an `action`: create, rename, assign, unassign, cover, merge, delete.
  Assignment takes `person_id` and `images: [{id, revision}]`; management operations
  take `person_id` and `version`. Merge also needs `target_id` and `target_version`.
- `GET /api/images?person={id}` intersects manual membership with other filters.
- `GET /api/detection?image_id={id}` returns settings/progress and optional regions.
- `POST /api/detection` supports enable (`enabled` boolean), detect (`image_id`,
  `revision`), queue, ignore (`image_id`, `revision`), and clear.
- `GET /api/images?view=review` lists current detections with no current manual labels.

### Catalog backup and restore

For a consistent SQLite-only backup (including People), run:

```powershell
.\.venv\Scripts\python.exe backup_catalog.py data/backups/catalog-before-people.sqlite3
```

The command refuses to overwrite a backup and uses SQLite's backup API, so it
includes committed WAL data even while the app is running. For a full restore
point, stop the app and copy the entire `data/` directory, including `vectors/`,
SQLite WAL/SHM files if present, OAuth files, and models. Keep that copy private.

To restore a full snapshot, stop the app, rename the existing `data/` directory
as a safety copy, and restore the saved directory as `data/`. To restore only the
catalog, stop the app, move the current catalog and any matching `-wal`/`-shm`
files to a safety folder, copy the backup as `data/catalog.sqlite3`, then restart
and rescan/reindex to reconcile image/vector state. Never replace a live database.
Deleting a person removes their manual labels only; originals remain intact.

Additional real-model verification:

```powershell
.\.venv\Scripts\python.exe verify_detection.py
```

This downloads a public-domain NASA fixture in memory and checks single/multiple
face regions, EXIF rotation, bounds, and a blank image. It never adds test photos
to your catalog. Live Drive and Cloudflare verification still requires your setup.

### Face recognition (ArcFace)

Optional face **recognition** groups matching faces into People albums.

- **Model.** InsightFace ArcFace `w600k_r50`, run locally with ONNX Runtime. Run
  `python download_arcface.py` to fetch it (resumable, with progress; `start.ps1`
  also offers this on first launch); it extracts
  `w600k_r50.onnx` into `data/models/` and checks the SHA-256
  `4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43`. You can also
  place `w600k_r50.onnx` there yourself. Its pretrained weights are licensed for
  **non-commercial research use only** — the download script prints this notice,
  and it is your responsibility to comply.
- **Pipeline.** YuNet detects each face and its five landmarks, a similarity
  transform aligns and crops the face to 112×112, and ArcFace produces a
  normalized 512-dimensional embedding. Everything runs on this PC; no face data
  leaves it.
- **Grouping.** Each new face is compared by cosine similarity to faces already
  assigned to people:
  - score ≥ **auto** threshold (default `0.50`) — added to that person automatically;
  - **review** threshold (default `0.35`) ≤ score < auto — listed as a suggestion to
    confirm or dismiss;
  - below review — a new `Unknown N` person is created (rename it later).

  Thresholds are configurable in **People → Face recognition (ArcFace)**. Manual
  labels remain authoritative: recognition only adds or links, and **Clear
  recognition results** removes embeddings and jobs without touching labels or
  originals.
- **Interface.** Enable, tune thresholds, queue the library, review suggestions,
  and clear from the People panel. The photo viewer lists recognized faces and
  lets you confirm or dismiss suggestions inline.
- **Storage.** New local tables `faces` (region, landmarks, embedding, person,
  score, status), `face_jobs` (resumable work), and `recognition_settings`, plus
  an `auto` flag on `people`. All local and deletable.
- **API additions.**
  - `GET /api/recognition?image_id=` — settings, progress, counts, and optional per-photo faces.
  - `GET /api/recognition/faces?image_id=` — recognized faces for one photo.
  - `GET /api/recognition/suggestions?offset=` — low-confidence matches awaiting review.
  - `POST /api/recognition` — actions `enable`, `settings`, `queue`, `detect`, `ignore`, `confirm`, `reject`, `assign`, `unlink`, `clear`.

Recognition is CPU-only and processed one photo at a time, so a large library
takes time; it resumes across restarts. False merges are possible, so keep the
review threshold conservative and correct mistakes from an album. Recognition is
unavailable (and clearly reported) until the ArcFace model is installed; all other
features work without it.


## Setup, sync and discovery

Open **Setup & background jobs** to inspect Drive sign-in, encoder/cache state,
actual loaded ONNX session providers, collection/backend, sync failures and job
history. **Download / prepare models** loads both towers in the background. Byte
progress measures additions to the local cache, not an exact download percentage.
`start.ps1` uses Python UTF-8 mode for SigLIP 2 tokenizer files on Windows.

Choose a dependency profile explicitly:

```powershell
.\setup-runtime.ps1 -Runtime cpu
# Or, with a compatible NVIDIA CUDA/cuDNN runtime already configured:
.\setup-runtime.ps1 -Runtime gpu
```

Startup preserves that profile. `IMAGE_INDEX_PROVIDER=cuda` rejects actual CPU
fallback; `auto` permits it. Docker builds select `IMAGE_INDEX_RUNTIME=cpu|gpu`
(default CPU); The GPU profile installs ONNX Runtime CUDA/cuDNN runtime extras. NVIDIA hardware, a compatible driver, and Docker GPU support must be provided by the host.
Existing Qdrant collections must match the chosen encoder's dimension and cosine
distance. Leave `QDRANT_COLLECTION` unset to retain separate per-model collections.

Automatic sync is opt-in in the setup dialog. Polling defaults to 300 seconds
(range 30–86400). Persisted Drive change checkpoints survive restarts; directory
moves trigger membership updates, inaccessible roots preserve the catalog, and
failed jobs back off up to one hour. Retry resets backoff; pause preserves the
last committed page. Added roots must be independent, with no selected parent/
child overlap. Initial listing and an explicit manual scan still read the full
selected tree. A lost change token triggers a fresh bootstrap.

Drive previews are normalized to JPEG and cached locally with a default 256 MiB
budget (0 disables caching, maximum 2048 MiB). Content revisions invalidate old
previews. The setup dialog shows usage and provides a clear-cache action. Original
photo downloads for inference/viewing are not saved by this cache.

**Timeline, albums & saved searches** provides monthly browsing and date ranges.
Capture dates preserve the camera-recorded date; modified dates use UTC. The
combined date source falls back to modified dates when capture metadata is absent;
capture-only mode offers an Unknown date group. Timeline uses names/tag search.
Ordinary albums are local, independent of folders and People. Select up to 100
photos, then **Edit selected photos** to replace tags, set favorites or add/remove
album membership. Undo survives restarts and refuses to overwrite newer edits or
changed images. Deleting an album retains every original photo.

**Find similar images** in the viewer reuses the selected photo's current vector,
excludes it from results, and respects active filters. Scores are cosine similarity,
not probabilities. **Compare similar copies** scans batches of 40 indexed photos
against their nearest vector candidates. It excludes exact-checksum copies and
allows confirmed/different reviews with undo. Reviews expire when photo content
changes. This is candidate discovery, not exhaustive duplicate detection; visually
similar scenes may score highly. It never deletes originals.

The PWA manifest supports installation over localhost or authenticated HTTPS.
Chrome may show **Install Imadex**; on iOS use Safari's Add to Home Screen. The
service worker caches only the empty interface, never API data, previews or full
photos. Offline use shows a connection notice and requires reconnection to browse.
An authentication failure clears the shell cache and visible gallery. Clear the
installed shell cache from setup. Phone authentication/installation still needs
verification on your actual Cloudflare hostname and device.

### Verified archive backup

For a catalog archive while the app runs:

```powershell
.\.venv\Scripts\python.exe backup.py create --data data --output ..\imadex-backup.zip
```

For catalog plus embedded vectors, stop the app and add
`--include-local-vectors`. The command acquires the store lock and refuses a live
vector-store copy. External Qdrant requires its own server snapshot. Archives
exclude OAuth credentials/tokens, models and regenerable thumbnails; keep the
metadata archive private and reconnect Drive after recovery.

```powershell
.\.venv\Scripts\python.exe backup.py restore --archive ..\imadex-backup.zip --target ..\imadex-restored-data
$env:IMAGE_INDEX_DATA = (Resolve-Path ..\imadex-restored-data).Path
.\start.ps1
```

Restore verifies checksums, archive paths and SQLite integrity before creating a
new directory. It refuses to overwrite an existing target. Without saved vectors,
queue indexing to rebuild the selected collection. With external Qdrant, restore
its matching snapshot separately and configure that server before starting.

Real-model check: `.\.venv\Scripts\python.exe -X utf8 verify_embeddings.py`.
SigLIP 2's 768-dimensional inference, text ranking and unchanged-image resume have
passed locally. Live Drive, Docker, CUDA and phone-tunnel acceptance remain separate
deployment checks.


Model-switching verification (temporary catalog, shared downloaded model cache):
` .\.venv\Scripts\python.exe -X utf8 verify_model_switching.py `.
Service-worker privacy checks: `node --test tests/sw.test.cjs`.
Full Python checks: `.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v`.

Local capture dates now use EXIF DateTimeOriginal, not the EXIF editing timestamp.
Rescan pre-existing local folders to populate that provenance; until then the
combined timeline falls back to file modification dates for legacy local rows.
Catalog scans, embeddings and Drive sync all appear in job history with processed/
failed counts. Private image responses use `no-store`; the bounded server-side
preview cache still makes repeated Drive browsing faster.


### NVIDIA inference container

Use the optional app GPU override (separate from Qdrant's GPU indexing profile):

```powershell
docker compose -f docker-compose.yml -f docker-compose.cuda.yml up --build
```

It selects the GPU dependency profile, requests one NVIDIA device, and defaults
to explicit CUDA so an actual CPU fallback is reported as an error. The pinned
ONNX Runtime 1.30 GPU wheel uses CUDA 13 and cuDNN 9; the GPU requirements file
installs its matching CUDA/cuDNN extras, and provider selection preloads those
libraries before session creation. See the [official ONNX Runtime CUDA requirements](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).
This does not install or upgrade your host graphics driver.

To verify an existing Qdrant server without writing into your image collections:

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_qdrant_server.py --url http://127.0.0.1:6333
```

The verifier creates one uniquely named temporary collection and removes that
collection at the end. It checks actual server writes, ranking, SQL gallery
filters, image-vector reuse, reopen/resume and missing-point cleanup with synthetic
vectors. Real encoder ranking is tested separately by the model verifiers. Set
`QDRANT_API_KEY` for an authenticated server; do not put credentials in the URL.


For a real GPU acceptance check that preserves your existing app runtime:

```powershell
.\.venv\Scripts\python.exe -X utf8 verify_gpu_runtime.py --prepare
```

This installs the GPU dependency profile into `data/gpu-verification-env`, loads
both encoders with explicit CUDA, and checks ranking and resume using temporary
fixture catalogs and the existing shared model cache. Runtime downloads can be
large. Later checks can omit `--prepare`.
Add `--real-models` to `verify_qdrant_server.py` to combine server checks with real
local encoder inference; otherwise it uses lightweight synthetic vectors.


### Optional text search (English first)

Install OCR on the PC with the project's Python 3.12 runtime:

```powershell
.\.venv\Scripts\python.exe -X utf8 setup_ocr.py
```

This creates `data/ocr-env` and leaves the embedding CPU/GPU runtime intact.
It installs RapidOCR 1.4.4 with bundled PP-OCRv4 detection/recognition and
orientation models, verifying their SHA-256 hashes. The model wheel is about
14.9 MB; the separate Python runtime/dependencies require additional disk space.
The [RapidOCR release license](https://github.com/RapidAI/RapidOCR/blob/v1.4.4/LICENSE)
and [PaddleOCR license](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE)
are Apache-2.0. This bundled recognizer also supports Chinese; the first
acceptance fixture and intended initial workflow are printed English.

In **Setup & background jobs**, enable automatic text extraction to process
current/new/changed images. Disable it to pause; progress resumes after restart
when enabled. **Retry failed extraction** resets failed rows; turn automatic
extraction on to process them. Alternatively, open a photo and choose
**Extract / refresh text** without enabling automatic library extraction.
Only the pending on-demand queue is transient; extracted results persist.

Select **Text in images** for OCR-only search, or **Names, tags & text** for
combined literal search. Words intersect with folder, person, album, date and
favorite filters. Text search matches substrings (English case-insensitive),
not semantic meanings. Visual search continues to use your existing encoder.
Saved searches retain the selected mode. Extracted text is selectable in the
viewer; empty results mean no confident printed text was found.

Inference uses one background worker and CPU sessions with two intra-op threads.
The existing checksum-verified loader applies orientation and preserves remote
originals. The worker receives only an in-memory PNG up to 1600 pixels per side,
with a 60-second timeout; it never saves source images. A generated English
invoice fixture took approximately 2.1 seconds including process/model startup
on this PC. That measurement is not representative of every personal image.
Small text, complex layouts, photos and handwriting may be inaccurate; each
image starts a fresh process to bound memory and isolate failures. No automatic
tags or face labels are created by OCR.

Text, errors, revision/model identity and timing live in `image_ocr` in the local
SQLite catalog. Content changes and missing images hide obsolete text until
re-extraction; model changes require fresh extraction. Text is private metadata
and is included in catalog backups. **Clear all extracted text** pauses OCR,
removes its catalog results and discards any in-flight result; existing backups
retain their own copy. It preserves original images, people labels, tags and
albums. Browser offline caches never contain OCR API results. Restore keeps
text metadata; reinstall the platform-specific OCR environment to extract more.

Container installations must create their own Linux OCR environment, rather
than reuse a Windows `data/ocr-env`. Run `python setup_ocr.py` inside the app
container after deployment; the base image includes OpenCV's GL runtime library.
Actual Docker and Cloudflare phone acceptance remain pending.

Real extraction/search smoke test using only temporary generated fixtures:
` .\.venv\Scripts\python.exe -X utf8 verify_ocr.py `.

## Performance diagnostics

Restart Imadex after updating, then open **Setup & background jobs → Performance diagnostics**.
Diagnostics are off by default. **Keep job summaries** records each embedding job; **Start capture**
also records individual spans for 1–30 minutes. Stopping or expiration ends detailed capture while
summaries continue. Uncheck summaries to stop logging entirely. Starting during a job produces a
partial capture; queue waiting is reported separately from active job time.

Enable resource sampling to collect GPU utilization/VRAM and process CPU/RAM every two seconds
during detailed capture. `nvidia-smi` requests time out after one second; missing metrics display
as unavailable. [psutil](https://pypi.org/project/psutil/) is included in both runtime profiles for
process metrics. Refresh dependencies with your existing CPU/CUDA runtime setup if needed; keep
the GPU profile when using CUDA. Process CPU can exceed 100% when several cores are active.

Start a capture before indexing new or changed images, then select the embedding job to inspect
Drive authentication/request/body reads, hashing, decoding, model loading, lock waits, embedding,
Qdrant upserts and SQLite commits. Counts separate inferred images, successful new images,
duplicate reuse, unchanged checks, failures and stale revisions. Rechecking ready images yields
no new-image throughput. Models and the first inference are timed separately when observed.
The largest stage identifies a candidate bottleneck; wall-time inference includes preprocessing
and CPU/GPU transfers, and GPU utilization includes other applications.

Stage totals exclude nested child spans; image totals are inclusive. Median/p95 use the most
recent 512 observations per stage, rather than an exact distribution over the entire library.
Job metadata contains the last observed input's bytes/dimensions/source and the loaded providers.
Uninstrumented time is shown as a residual. Timing does not prove every ONNX operation ran on CUDA.

**Export diagnostics** downloads the current bounded summary and resource samples as JSON through
the authenticated API. **Clear diagnostics** requires confirmation, disables logging and deletes
the retained diagnostic files. Exported numeric IDs identify records within this catalog; review
reports before sharing. Logs contain no filenames, paths, Drive IDs/URLs, checksums, query/OCR/face
content, tokens, credentials or raw exception messages. No diagnostic data is sent externally.

Detailed JSONL spans and job summaries live in ignored `data/performance/`, capped at five 10 MiB
files, and are excluded from normal backup archives. A bounded background writer drops events
when overloaded and reports dropped events/write errors without stopping indexing. The dashboard
keeps at most 20 recent summaries in memory and reads retained history once at startup. Diagnostic
APIs are never cached by the installed app; sign-in failure clears visible diagnostic data.

Read recent retained job summaries from the PC:

```powershell
.\.venv\Scripts\python.exe performance.py --data data
```

Compare logging overhead using generated fixtures in a temporary catalog and vector store:

```powershell
.\.venv\Scripts\python.exe verify_performance.py --real-models --provider cuda --model siglip2 --repeats 3
```

Use `--provider cpu` for CPU verification. Without `--real-models`, the command uses a synthetic
20 ms encoder. Models are warmed before comparison; mode order rotates across repetitions.
It verifies matching vectors and ready counts across off/summary/detailed modes. Existing model
cache may be used or populated; personal catalog/vector collections and original photos remain
untouched. Real Drive/CUDA performance must be measured with a representative personal-library
capture; generated fixtures establish instrumentation behavior, not your Drive bottleneck.
