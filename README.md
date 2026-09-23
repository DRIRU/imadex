# imadex

A local-first personal image library. `imadex` runs a small dashboard on your PC,
keeps the original photos in **Google Drive** (or on local disk), and builds a
private **CLIP embedding index** on this machine so you can find pictures by
describing them in plain English.

It recursively indexes folders, filters by image format, streams previews and
originals without copying them to disk, and tracks favorites, tags, and exact
duplicate copies. Nothing is sent to an external inference API — only Google
Drive access needs the network.

> The web UI is branded **"frame"**. The repository and Python package are named
> `imadex`; the two refer to the same application. Tunnel-mode login username is
> `frame`.

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
  and rank your collection by CLIP cosine similarity.
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
| `fastembed`               | 0.8.1       | ONNX CPU inference for CLIP image/text            |
| `qdrant-client`           | 1.19.1      | Embedded vector store (local mode)                |
| `opencv-python-headless`  | 4.11.0.86   | Optional local YuNet face-region detection        |

---

## Quick start

```powershell
cd C:\Users\Jezt\Documents\sangeeth\imadex
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:8765>. Alternatively run `start.ps1`, which creates the
virtual environment on first launch and then starts the server.

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
  catalog, Qdrant vectors, thumbnails, CLIP weights, and OAuth files survive
  rebuilds. Put `data/google-client.json` on the host before connecting Drive.
- **First run downloads ~600 MB** of CLIP weights (and a ~233 KB YuNet model on
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
├─ semantic.py             CLIP image/text embeddings + Qdrant vector search
├─ people.py               Manual People albums: labels, covers, merge/delete
├─ detection.py            Optional local YuNet face-region detection
├─ backup_catalog.py       Consistent SQLite-only catalog backup
├─ verify_embeddings.py    Real-model smoke test (CLIP + Qdrant, temp catalog)
├─ verify_detection.py     Real YuNet face-detection smoke test
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
│  └─ test_people.py       People labels, filters, and detection state
└─ data/                   Runtime state — created on first launch (gitignored)
   ├─ catalog.sqlite3      Metadata source of truth (WAL)
   ├─ vectors/             Qdrant local collection
   ├─ models/              Cached CLIP and YuNet ONNX weights
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

**Models.** [FastEmbed](https://qdrant.github.io/fastembed/) 0.8.1 using
`Qdrant/clip-ViT-B-32-vision` for pixels and the paired
`Qdrant/clip-ViT-B-32-text` for search descriptions. Both emit 512-dimensional
vectors in the same space; vectors are normalized and ranked by cosine
similarity. Inference runs on the CPU via ONNX Runtime using up to four threads.

See FastEmbed's [image support](https://qdrant.github.io/fastembed/examples/Image_Embedding/)
and [supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/).

**Pipeline:**

1. Scan a selected folder (and subfolders) to collect file IDs, metadata, and
   content checksums.
2. Queue new or changed images. Drive originals are fetched into memory through
   the authenticated API, verified against their checksum when available, EXIF
   re-oriented, and converted to RGB. Local images are read from their paths.
3. Generate a CLIP image vector locally. Only vectors and processing state are
   persisted; Drive originals are not written to disk. Identical bytes reuse an
   existing embedding.
4. Encode the search description with the paired CLIP text model and ask Qdrant
   for the closest image vectors. Folder, format, favorites, and duplicate
   filters still apply; removed/outdated vectors are excluded.

Each embedding carries a fingerprint that includes the model/preprocessing
version, so changing content or the model invalidates stale vectors. The
**Visual search** panel shows ready/total counts, progress, and per-file
failures; **Index images** backfills and retries failures. Existing vectors
survive restarts.

Model weights download on first use into `data/models/`. Subsequent inference is
fully offline except for Google Drive access.

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
- Embedding uses CLIP resize/crop preprocessing, so tiny details and text can be
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
```

Unit/integration tests use temporary images, a real local Qdrant store, and
deterministic model substitutes for lifecycle tests. `verify_embeddings.py`
separately runs **actual** CLIP image and text inference, verifies
color-description ranking, and checks incremental resume against a temporary
catalog and the shared model cache. Drive downloads are simulated in tests;
live Google OAuth, folder access, and private image streaming require your own
credentials and sign-in.

---

## Troubleshooting

- **"Embedding service is not started."** Run through `start.ps1` or the venv
  Python; the embedding dependencies are required.
- **First visual search is slow.** The CLIP weights (~600 MB) download on first
  use into `data/models/`. Later runs are offline.
- **"Could not load CLIP."** Check the internet connection on first use and
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
[FastEmbed](https://github.com/qdrant/fastembed) (ONNX Runtime, CLIP ViT-B/32) ·
[Qdrant](https://qdrant.tech/) local mode · SQLite · OpenCV (YuNet face
detection) · vanilla HTML/CSS/JS front end · Google Drive API (OAuth 2.0 + PKCE) ·
Cloudflare Tunnel for remote access · Docker / Docker Compose (optional).


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
