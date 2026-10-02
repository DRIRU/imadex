# Personal image index — plan and progress

Last updated: 2026-10-02 (Asia/Calcutta)

## Working agreement

Keep this file updated whenever the plan, progress, verification results, or remaining work changes. The application now lives in this `imadex` directory (previously `index`). Preserve other ongoing edits.

## Objective and decisions

- Original images remain in Google Drive; the dashboard runs on this PC.
- Phone access uses Cloudflare Tunnel with authenticated access.
- Image-content embeddings and natural-language visual search are core functionality.
- Qdrant local mode persists vectors; SQLite stores metadata and embedding job state.
- Paired image/text models run through FastEmbed/ONNX Runtime, producing normalized vectors ranked by cosine similarity. Originally CLIP ViT-B/32 (512-d); SigLIP 2 base (768-d) is now the default (see the "Image encoder" section).

## Completed

- [x] Local catalog, recursive Drive/local scanning, previews, tags, favorites, and exact duplicates.
- [x] Read-only Drive OAuth flow with PKCE and setup guidance.
- [x] Mobile bottom navigation, folder selection, touch controls, full-screen viewer, swipe navigation, thumbnail-first loading, and expandable statistics.
- [x] Configurable Cloudflare HTTPS origin, password authentication on all routes, origin checks, and tunnel setup instructions.
- [x] Install embedding dependencies in `.venv/` and download both CLIP models into `data/models/`.
- [x] Implement persistent Qdrant vectors in `data/vectors/`.
- [x] Read original bytes into memory, verify available checksums, normalize orientation/color, and embed locally without saving Drive originals.
- [x] Automatically queue embeddings after scans and resume unfinished work on startup.
- [x] Reuse unchanged/duplicate embeddings; invalidate changed images; exclude missing/stale vectors; expose retryable failures.
- [x] Add default visual search, literal Names & tags mode, progress/retry controls, and relative similarity scores.
- [x] Document architecture, setup, data locations, operating limits, and verification commands.

## Verification completed

- [x] 13 automated tests passed: catalog, tunnel authentication, real Qdrant persistence/ranking/filtering, resume, duplicate reuse, failures/retries, and simulated Drive downloads.
- [x] Actual CLIP inference ranked matching red, blue, and green image fixtures first for their corresponding descriptions.
- [x] An unchanged second embedding pass processed zero images.
- [x] Browser search for “a solid red image” ranked the red sample first despite generic filenames, verifying the HTTP/UI semantic-search path.
- [x] Earlier mobile layout verified at 320px and 390px without horizontal overflow.

## Final verification and handoff

- [x] Embedding controls and actual semantic results checked at 390px; the blue description ranked the blue sample first. No horizontal overflow.
- [x] Names & tags search returned the expected exact filename; 320px layout also had no horizontal overflow.
- [x] All 13 tests and the actual CLIP smoke test passed again from the renamed `imadex` directory.
- [x] Main dashboard restarted with `.venv/Scripts/python.exe`; `/api/embeddings` confirms Qdrant local, 512 dimensions, no service error, and zero personal images awaiting Drive connection.
- [x] Temporary QA server and browser tab closed; browser viewport reset. User catalog was not populated with test images.
- [x] Persistent instruction added in `AGENTS.md` to keep this plan updated on future work.
- [x] Added `GEMINI.md`, `CLAUDE.md`, and `OPENCODE.md`, each directing its assistant to the canonical `AGENTS.md` and the shared `plan.md`. AGENTS.md records the requirement to maintain these pointers.

Embedding implementation is complete and available at http://127.0.0.1:8765. The remaining steps below are external account/tunnel configuration, not unfinished embedding code.

## User setup still required

- Google OAuth client file/sign-in have not been provided. Actual Drive images have not been indexed or embedded yet.
- Cloudflare Tunnel is not live. Its hostname/connector and app password still need configuration.
- The ArcFace model is required for face recognition. `start.ps1` now offers to download it on first run; it can also be fetched with `download_arcface.py`. Detection and all non-recognition features work without it.

## Limits and future work

- CPU inference; no external inference API. Short English descriptions work best.
- No OCR or automatic tagging. Optional local face recognition (ArcFace) was added later; see the "Face recognition (ArcFace)" section.
- 64 MB embedding-input limit; unsupported formats are reported as failures. Animated/multipage images use the first frame.
- Embedded Qdrant targets a personal library; larger collections may need a dedicated Qdrant server.
- Folder scans are manual; each scan automatically queues new/changed embeddings.

## People gallery discussion — 2026-09-23

- Rechecked the live `/api/embeddings` endpoint: Qdrant local, CLIP 512 dimensions, no error; total/ready/pending/failed are all zero. Implementation is ready, but the personal Drive library still needs connection and its first scan.
- User requested an iOS-style People gallery. This was implemented (manual labels, YuNet detection, and later ArcFace recognition); the notes below are historical.
- Available next scope: local face detection, manual person labels, and mobile People albums with rename/remove controls. Automatic identity matching and a facial-recognition database were originally out of scope, but optional local ArcFace recognition with auto-grouping was later implemented at the user's request (see below).
- Keep originals in Drive and any manual labels in the local catalog. Superseded: this was implemented (see below).

## People gallery implementation plan

Status: implemented and verified. The unchecked items below are the original plan, kept for reference; see "Implementation progress" and the later "Face recognition (ArcFace)" sections for actual results.

### Scope and expected behavior

- Add a People section containing albums assembled from explicit user labels.
- A photo can contain multiple people. The user selects existing names or creates new ones; no person is inferred from appearance.
- Optional local face detection can highlight regions that may contain faces to help the user review a photo. Detection does not assign or suggest names.
- A new photo starts without person labels. The user can label multiple deliberately selected photos together to reduce repeated work.
- Keep the existing CLIP visual-search pipeline and Qdrant collection intact. People filtering uses manual SQLite relationships, not CLIP similarity.
- No face identity embeddings, automatic cross-photo matching, or recognition-database work is included in this plan as originally written. Optional local ArcFace recognition was later added at the user's request; see "Face recognition (ArcFace)".

### Phase 1 — Persistent manual people labels

- [ ] Add an idempotent SQLite migration with a schema version and a documented backup/restore procedure for the catalog.
- [ ] Add `people`: stable ID, display name, optional cover-image ID, created/updated timestamps. Names are not identity keys; allow different people with the same name.
- [ ] Add `image_people`: image ID, person ID, creation timestamp, and a unique constraint on the pair. Enable foreign-key enforcement on each connection or provide equivalent transactional integrity.
- [ ] Add a service module for create, rename, assign, unassign, merge, and delete operations. Validate IDs and name lengths; parameterize database queries.
- [ ] Deleting a person removes only that person's labels. Deleting, merging, or renaming people must never modify original images.
- [ ] Merge only user-selected people, deduplicate shared photo memberships, and perform the operation in one transaction. Show the affected counts before a merge or delete.
- [ ] Preserve labels across file renames when the catalog retains the same image ID. Do not automatically transfer labels to different files or duplicate copies.
- [ ] Associate labels with the current content fingerprint: changed image contents require review before old labels are shown in albums; missing images are hidden. Document that current local rename handling may create a new image ID and require manual reassignment.

Acceptance: create two people, label one photo with both, restart the app, and confirm both albums still contain it. Remove one label without affecting the other or the file.

### Phase 2 — API and gallery integration

- [ ] Add authenticated `GET /api/people` for people, album counts, and cover-image references; support name search and pagination.
- [ ] Add `GET /api/images/{id}/people` and bounded JSON mutation endpoints for person management and manual photo assignments. Match the existing Host, Origin, and authentication checks.
- [ ] Support a person filter in `/api/images`, intersecting it with folder, favorites, format, and existing text/visual search filters before pagination/ranking.
- [ ] Deduplicate album photos even when the same photo has several labels. Keep album counts consistent with missing/stale-image filtering.
- [ ] Bound bulk operations (initially 100 explicitly selected image IDs per request) and make repeat assignment/removal requests idempotent.
- [ ] Return actionable validation errors for deleted people, unavailable images, and stale photo revisions; never silently label a changed photo.

Acceptance: a person filter combined with folder and visual search returns only photos the user labeled with that person. Unauthenticated tunnel requests cannot read or change labels.

### Phase 3 — Mobile People UI

- [ ] Add People to desktop navigation and the phone navigation pattern without crowding existing controls.
- [ ] Show name, photo count, and a user-selected cover photo on each person card; use a placeholder when no cover is available.
- [ ] Open a person album in the existing paginated gallery and full-screen photo viewer.
- [ ] Add a searchable name picker to image details with existing-label chips, create-name, assign, and remove actions. Explain that labels are manual.
- [ ] Add explicit photo multi-select and a batch-label action; show the number of selected photos before applying names.
- [ ] Add rename, cover selection, merge, and delete controls with concrete previews for destructive metadata changes.
- [ ] Provide loading, empty, failure, and retry states. Preserve selection on recoverable request failures.
- [ ] Use at least 44px touch targets, visible focus, accessible names, and a phone-friendly dialog/sheet. Keep labels and buttons usable with long names and the on-screen keyboard.

Acceptance: at 320px and 390px widths, create a person, tag selected photos, open their album, rename them, and remove a label without horizontal scrolling. Verify keyboard-only use on desktop.

### Phase 4 — Optional local face detection assistance

- [ ] Choose and document a CPU face detector after verifying its official distribution, model license, input requirements, download size, and compatibility with the existing environment. Pin the dependency and model version/checksum.
- [ ] Add explicit enable/disable controls. Download detector weights locally; run detection on the PC using the existing bounded, checksum-verified image loader.
- [ ] Store detection job state and geometric regions only: image ID, content fingerprint, detector version, normalized box coordinates, and detection confidence. Store no face identity vectors.
- [ ] Normalize EXIF orientation before detecting and drawing boxes. Limit analysis resolution and validate/clamp coordinates before rendering.
- [ ] Start with on-demand detection in the viewer. Add a resumable background queue only after the on-demand path works, with progress, retry, and concurrency limits so browsing remains responsive.
- [ ] Offer a reviewed-images view for photos with detected faces but no manual people labels. Do not imply that every face in a partially labeled photo has been accounted for.
- [ ] Let the user ignore incorrect detections and label photos even when detection misses a face. Detection results must never create person labels.
- [ ] Invalidate stale regions after content/model changes. Disabling assistance stops new detection; provide a separate control to clear detection metadata without removing manual labels.

Acceptance: rotated, multi-face, and no-face fixtures render correctly; failed downloads/inference can be retried; no detection action automatically adds a name. Drive originals remain remote and are not persisted by detection.

### Phase 5 — Verification, documentation, and rollout

- [ ] Add meaningful migration and service tests for persistence, many-to-many labels, same-name people, merge deduplication, deletion, batch idempotency, and stale/missing content.
- [ ] Test API authorization, person/search filter intersections, pagination, invalid IDs, batch limits, and concurrent edits.
- [ ] Test detection geometry, orientation, invalidation, and failure recovery with synthetic/mocked data; run a real-detector smoke test with a suitable licensed fixture. Do not populate the personal catalog with QA fixtures.
- [ ] Re-run the existing catalog and embedding tests using `.venv/Scripts/python.exe`, plus the CLIP smoke test if changes affect that pipeline.
- [ ] Verify the full mobile flow locally and over the configured Cloudflare Tunnel once available, including slow image loads and expired authentication.
- [ ] Document all new metadata locations, manual-label behavior, detector setup/limits, backup/restore, and how to clear labels/detections without deleting originals.
- [ ] Update this plan after each phase with actual verification results, known issues, and remaining tasks. Keep assistant instruction files pointing to AGENTS.md and this plan.

### Delivery order and dependencies

1. Deliver phases 1–3 as the first useful release: manual People albums work without any detector or extra model download.
2. Add phase 4 as optional review assistance, then finish phase 5 validation for that feature.
3. Use a temporary local fixture library during development. Real-library acceptance still needs Google OAuth setup and a first Drive scan; phone-over-tunnel acceptance needs the Cloudflare hostname/connector and app password.

Definition of done: manual People albums are persistent, correctable, searchable, and usable on a phone; originals are unchanged; existing visual search still works; detection, if enabled, only highlights possible face regions and never identifies people.


### Implementation progress — 2026-09-23

- Manual People service, versioned SQLite schema, authenticated API, people filters, mobile albums, batch labeling, cover selection, and management dialogs are implemented.
- Optional YuNet region detection is implemented with checksum-verified model download, persisted jobs, retry, cancellation, clear/ignore controls, and an unlabeled-photo review view.
- 20 automated tests pass, including all 13 pre-existing tests. Real YuNet checks passed on portrait, two-face, and no-face fixtures.
- Browser checks at 390px passed for batch assignment, album count, rename, cover selection, and label removal; no horizontal overflow in the management dialog.
- Remaining validation: complete phone detection review, 320px/keyboard checks, regression smoke checks, documentation review, and main-server restart. Live Drive/tunnel verification remains dependent on user setup.

## Docker packaging — 2026-09-23

Status: files added, not yet built or verified locally (Docker is not installed on this machine).

- Added `Dockerfile` (python:3.12-slim, `libgomp1` + `libglib2.0-0` for ONNX Runtime/OpenCV, installs pinned `requirements.txt`).
- Added `docker-compose.yml`: single `imadex` service, `./data:/app/data` volume, host publish `127.0.0.1:8765` (loopback-only), tunnel-mode env passthrough, and an HTTP healthcheck that tolerates Basic-auth 401.
- Added `.dockerignore` (excludes `data/`, secrets, caches, logs, VCS, assistant docs) and `.env.example` for tunnel mode.
- Added `--host` / `IMAGE_INDEX_HOST` to `app.py` (default unchanged: `127.0.0.1`); the container passes `--host 0.0.0.0`. Host/Origin allowlisting still restricts requests to loopback or the configured public origin, so the security posture is unchanged.
- Updated README with a "Run with Docker" section, dependency table, project layout, config reference, and tech stack.

Remaining: user to run `docker compose up --build` and confirm build, first-run model download into the volume, local dashboard access, tests-in-container, and tunnel mode if desired.

## Face recognition (ArcFace) — 2026-09-23

Status: implemented; unit tests pass and real ArcFace inference verified on 2026-09-23.

- User approved ArcFace/InsightFace as the model and "auto grouping for high confidence, ask for low confidence" as the scope.
- `detection.py` refactored to expose shared `load_model` and `detect_faces` (returns the resized BGR frame plus box, confidence, and 5 landmarks) while `Detector.detect` behavior is unchanged.
- New `recognition.py`: onnxruntime ArcFace `w600k_r50`, 112×112 similarity alignment from YuNet landmarks (Umeyama, no reflection), normalized 512-d embeddings, cosine matching, auto/review thresholds, `Unknown N` auto-people, resumable `face_jobs` worker, and the `action`/`status`/`suggestions`/`image_faces` API surface.
- New tables `recognition_settings`, `faces`, `face_jobs`, plus an additive `people.auto` column (schema version 1 retained).
- Wired into `app.py`: `GET/POST /api/recognition`, `GET /api/recognition/faces`, `GET /api/recognition/suggestions`; recognizer starts with the app and re-queues after scans.
- UI: People panel "Face recognition (ArcFace)" controls (enable, thresholds, queue, clear, suggestion review) and a recognized-faces list in the viewer.
- `download_arcface.py` fetches the model with progress and resume support, pins the extracted file SHA-256, and prints its non-commercial license notice; `verify_recognition.py` runs a real YuNet+ArcFace smoke test when the model is present.
- Tests: `tests/test_recognition.py` adds 10 tests (auto grouping, low-confidence suggestion confirm/reject, new-person creation, clear-keeps-labels, stale source, threshold validation, missing model, alignment, HTTP auth). Full suite: 33 tests pass.
- Licensing note: InsightFace pretrained weights are non-commercial research only. The app never downloads them silently; `start.ps1` asks once before fetching, and the standalone `download_arcface.py` is always available.
- `start.ps1` now forwards extra arguments to `app.py` (for example `.\start.ps1 --port 8766`) and, when `data/models/w600k_r50.onnx` is missing, prompts once to download the ArcFace model (defaulting to "no").

Remaining: confirm real grouping quality on the user's own photos, tune threshold defaults (auto 0.50 / review 0.35), check CPU timing on a real library, and verify the mobile layout for the new controls.

## Verification results — 2026-09-23 (recognition)

- Downloaded `buffalo_l.zip` (275.3 MB) via `download_arcface.py`; extracted `w600k_r50.onnx`, SHA-256 `4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43`; ONNX input `['None',3,112,112]`, output `[1,512]`.
- `verify_recognition.py` passed: 6 faces detected in the fixture, self-similarity 1.0000, cross-face similarity 0.0437 (clear separation between identities).
- Full automated suite: 33 tests pass (23 prior + 10 recognition).
- Download throughput from GitHub release CDN measured at ~0.5 MB/s from this location, so the initial 275 MB fetch takes ~9 minutes; the script now resumes and shows progress.

## Optional GPU acceleration — 2026-09-23

Status: implemented; CPU remains the default and no behavior changes unless a GPU build is installed.

- Added `accel.py`: selects ONNX execution providers from `IMAGE_INDEX_PROVIDER` (`auto` default, `cpu`, `cuda`/`gpu`). `auto` uses CUDA when the installed onnxruntime offers it and falls back to CPU; an explicit `cuda` request with the CPU build raises a clear error.
- `semantic.py` (CLIP) and `recognition.py` (ArcFace) now request `accel.providers()` instead of hard-coded CPU, and both status endpoints report the active `provider`.
- UI shows GPU/CPU in the Visual search and People status lines.
- This machine has an NVIDIA GTX 1650 (4 GB) but the installed onnxruntime is the CPU build (`get_available_providers()` = Azure + CPU), so indexing currently runs on CPU. Enabling the GPU requires installing `onnxruntime-gpu` (conflicts with `onnxruntime`) plus CUDA/cuDNN, then setting `IMAGE_INDEX_PROVIDER=cuda` (or leaving `auto`).
- Qdrant local mode and the pip OpenCV build are not GPU-accelerated; only CLIP/ArcFace inference is.
- Tests: `tests/test_accel.py` (4 tests) covers cpu/auto/explicit-cuda selection and fallback. Full suite: 37 tests.

Remaining: on a GPU-enabled install, confirm `provider` reports CUDA, compare indexing speed, and document any CUDA/cuDNN version requirements.

## External Qdrant server — 2026-09-23

Status: implemented; not yet exercised against a live server.

- `semantic.py` now selects the vector backend at startup: `QDRANT_URL` (with optional `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `QDRANT_TIMEOUT`) connects to an external server; when unset it keeps embedded local mode in `data/vectors/`. The active backend is reported in `/api/embeddings` as `database`.
- `docker-compose.yml` gains two optional profiles: `qdrant` (CPU, `qdrant/qdrant:latest`) and `qdrant-gpu` (`qdrant/qdrant:gpu-nvidia-latest` with `QDRANT__GPU__INDEXING=1` and an NVIDIA device reservation), both persisting to `data/qdrant`. `.env.example` documents `QDRANT_URL=http://qdrant:6333` for Compose.
- Tests: `tests/test_qdrant_config.py` (3 tests) verifies server URL/API key selection, custom collection name, and local default. Full suite: 40 tests.

Qdrant GPU facts confirmed from the official docs (v1.13+): GPU accelerates indexing only (not search); GPU builds are Linux x86_64 Docker images only (`gpu-nvidia`/`gpu-amd`) and require the NVIDIA container toolkit; each GPU handles up to 16 GB of vectors per indexing iteration; Qdrant's GPU path uses Vulkan, so neither embedded local mode nor the pip OpenCV build is GPU-accelerated.

Remaining: verify against a running Qdrant server (CPU and, on a Linux host, GPU), confirm index/query correctness with a real library, and note any migration steps when switching stores.

## Image encoder: SigLIP 2 default — 2026-09-23

Status: implemented; the full automated suite is green. Real SigLIP 2 download/inference has not been run in this environment (the ~1.5 GB fetch is slow here); the CLIP path remains verified.

- `semantic.py` gained a model registry and `select()`. `IMAGE_INDEX_MODEL` (`siglip2` default, `clip` optional) chooses the FastEmbed text+image pair, dimensions, model version, and Qdrant collection (`images_siglip2_base_v1`, 768-d by default; `images_clip_b32_v1`, 512-d).
- No new dependency: FastEmbed 0.8.1 already ships `google/siglip2-base-patch16-224` for both towers (Apache-2.0, ~1.5 GB; vision 0.37 GB + text 1.13 GB). GPU/CPU provider selection and `/api/embeddings` fields are unchanged.
- Switching models changes the fingerprint/model version, so images re-embed into the new collection; the previous collection is retained so switching back is instant.
- Config/packaging: `IMAGE_INDEX_MODEL` is passed through Docker Compose and documented in `.env.example`.
- Tests made dimension-agnostic (`test_semantic.py`, `test_qdrant_config.py`) plus new `tests/test_semantic_models.py`.
- Docs: README model paragraph, config reference, download sizes, and troubleshooting updated.
- Remaining: run `verify_embeddings.py` with SigLIP 2 to confirm 768-d ranking and resume; compare CPU timing/quality vs CLIP; optionally surface the active model name in the Visual search panel.

## Web rebrand (Frame → imadex) — 2026-09-23

Status: done. Visible branding changed to **imadex** (page title, sidebar brand, Drive-connect messages, Basic-auth realm, tunnel prompts) in `web/index.html`, `web/app.js`, `app.py`, and `start-tunnel-access.ps1`. The Basic-auth **username `frame`** and the `FRAME_PASSWORD`/`FRAME_PUBLIC_ORIGIN` environment variables were kept for backward compatibility. README notes this.

## Phase 2 design: DINOv3 visual similarity (not implemented)

Purpose: image→image only (vision-only, no text tower). Enables "Find similar", visual near-duplicate grouping, and visual clustering — a separate axis beside text search, not a replacement for SigLIP 2/CLIP.

Open decision to settle first: the runtime path. DINOv3 is not in FastEmbed, so either use an ONNX export (verify availability/quality) or add `transformers` + `torch` (a large new dependency). ONNX is preferred to keep the stack lightweight.

- Model: `facebook/dinov3-vitb16-pretrain-lvd1689m` (86M params, 768-d) or ViT-S/16 (21M, 384-d, CPU-friendlier); use the pooled/CLS token, L2-normalized.
- License: the custom DINOv3 License. Commercial use is allowed, but it requires (a) accepting the gated license on Hugging Face with an account/token, (b) redistribution of the materials/derivatives under the same terms, and (c) a prominent "Built with DINOv3" attribution in the UI/docs.
- Storage: mirror the embedding lifecycle — a `dino_embeddings` table (image_id, fingerprint, model, status, error, updated) plus collection `images_dinov3_v1`; reuse the bounded image loader and `accel`.
- Code: new `dinov3.py` (`DinoIndex` worker with enable/queue/clear, status, and by-image search), `download_dinov3.py` (gated download requiring explicit acceptance), and `verify_dinov3.py`.
- API: `GET /api/dinov3` (status), `POST /api/dinov3` (enable/queue/clear), `GET /api/similar?image_id=&limit=` (ranked visually similar images).
- UI: "Find similar" action in the photo viewer; results in the existing paginated gallery; opt-in enable with progress/retry like the other pipelines.
- Effort/risk: much larger than Phase 1 — new model runtime, gated weights, and a new search UI. Best done after Phase 1 is validated against a real library.

## Manual changes studied — 2026-10-02

User requested review and understanding of their manual changes. Application code and configuration were left unchanged; only this progress record was updated.

- Reviewed committed changes through `8e9b4b4`; the working tree was clean before this review.
- Current architecture: SigLIP 2 is the default image/text encoder (768 dimensions), CLIP remains selectable (512 dimensions), Qdrant can be embedded or external, and ONNX provider selection is configurable. Docker packaging and the Imadex rebrand are present. The separately added ArcFace feature is recorded in the project history. DINOv3 remains a design only.
- Confirmed the installed FastEmbed registry includes both SigLIP 2 towers with 768 dimensions (vision 0.37 GB, text 1.13 GB).
- Ran 34 tests covering catalog/authentication, manual People labels, face-region detection state, semantic indexing/search, model selection, Qdrant configuration, and provider selection: all passed. Recognition tests and real identity inference were not run as part of this review.
- Installed ONNX Runtime is 1.30.0 and advertises Azure/CPU providers; CUDA is unavailable in this environment. Docker is not available on the current PATH. No models were downloaded, servers restarted, or personal-library state changed.
- Real SigLIP 2 inference, a live external Qdrant server, Docker execution, and CUDA inference remain unverified here. Historical test counts above refer to earlier checkpoints.

### Review observations for later work

- `requirements.txt` pins the CPU `onnxruntime` package and `start.ps1` reinstalls requirements on every launch. The documented manual GPU-package swap therefore needs a separate dependency/startup path to avoid reinstalling the CPU package beside it.
- Provider status is derived from `get_available_providers()` rather than the loaded inference session; a CUDA-capable package can be reported as GPU even if session initialization falls back to CPU.
- A fixed `QDRANT_COLLECTION` override bypasses per-model collection naming, and startup does not validate an existing collection's vector dimensions. Switching between 512-d and 768-d models with the same override can fail on writes. `.env.example` still suggests a CLIP collection despite the SigLIP 2 default.
- The Compose healthcheck catches every HTTP error as success, including 404/500, although its intended exception is authenticated 401.
- Compose does not forward `IMAGE_INDEX_PROVIDER` or `QDRANT_TIMEOUT`. The README's external-Qdrant paragraph still names the CLIP collection as the default.
- The model-selection tests assume the default model even when `IMAGE_INDEX_MODEL=clip` is explicitly configured; a test run under that supported configuration will need environment isolation.

These are recorded findings, not fixes. Continue from the user's current implementation and preserve their commits.

## Suggested next stages — 2026-10-02

Status: stages 1–3 authorized for implementation on 2026-10-02; work is active. The full scope below remains the completion target.

### Stage 1 — A reliable personal-library release (recommended first)

- [ ] Fix the configuration gaps recorded in the review: CPU/GPU dependency paths, actual session-provider reporting, Qdrant model/dimension validation, Compose environment passthrough and healthcheck, and environment-isolated model tests.
- [ ] Add a setup/status page that reports Drive connection, active encoder, model availability/download progress, actual inference provider, vector backend, and actionable failures. Never expose tokens, passwords, or API keys.
- [ ] Verify real SigLIP 2 image/text inference and model switching against a temporary catalog; verify an external Qdrant server and Docker on an available installation.
- [ ] Connect a small real Drive folder, confirm original-file reads and search quality, measure initial/incremental indexing time, and test interrupted-work recovery. Expand the library only after this pilot passes.
- [ ] Validate authenticated Cloudflare access from the phone, including slow network behavior and expired authentication; document the working deployment.
- [ ] Make a catalog/data backup and demonstrate recovery with a temporary copy. Document vector-store changes and reconciliation.

Acceptance: a real Drive pilot is searchable on PC and phone, an unchanged scan avoids new embedding inference, interrupted work resumes, and reported model/provider/backend match the running services.

### Stage 2 — Keep the library current

- [ ] Add incremental Drive synchronization with persisted change cursors and safe handling of renamed, changed, moved, and removed files within selected roots.
- [ ] Offer configurable polling, pause/resume, bounded retries/backoff, and per-stage progress. Failed scans must not mark the library missing.
- [ ] Cache private thumbnails with a bounded storage budget and content-based invalidation to improve phone browsing; retain Drive originals remotely.
- [ ] Add a job history with completed/failed counts and durations so indexing problems are easy to diagnose.

Acceptance: selected folders update without routine full rescans; reconnecting or restarting preserves sync state; revoked access produces a clear recovery action.

### Stage 3 — Better gallery discovery

- [ ] Add date ranges, a timeline/calendar view, and saved searches. Distinguish capture date from file-modified date and handle missing dates.
- [ ] Add ordinary albums independent of Drive folders, with bulk tags/favorites and undo for metadata edits.
- [ ] Add a mobile installable PWA shell with explicit authentication handling. Any offline cache of private photos must be an explicit setting, clearable by the user.
- [ ] Add “Find similar” for general image-content similarity using the existing SigLIP 2/CLIP vectors first; exclude the selected image, respect filters, and label scores as similarity rather than probabilities.
- [ ] Evaluate visual near-duplicate suggestions separately from exact checksum duplicates; provide comparison/review before any action. Do not delete originals automatically.

Acceptance: common browsing tasks work with one hand on a phone, saved searches are persistent, and image-similarity results are useful on the real library without another model download.

### Stage 4 — Optional deeper image search

- [ ] Consider local OCR for screenshots/documents and combine its text with filename/tag search. Verify language support, model license, and measured CPU cost before choosing a runtime.
- [ ] Revisit the existing DINOv3 design only after measuring whether the current image-to-image search needs improvement. Keep that model optional, and verify its distribution/license/runtime before implementation.

Suggested immediate scope: Stage 1. The later stages can be selected independently after the pilot identifies the user's most frequent tasks.

### Active implementation checkpoints — 2026-10-02

- [ ] Reliability: dependency profiles, session-provider reporting, collection validation, Compose fixes, setup/status page, model and deployment verification, backup/restore exercise.
- [ ] Synchronization: persisted Drive change cursors, selected-root membership, pause/resume, retry/backoff, private bounded thumbnail cache, job history.
- [ ] Gallery: dates/timeline, custom albums, saved searches, bulk metadata edits/undo, mobile PWA with authentication-aware caching, image-content similarity and near-duplicate review.
- Live pilot prerequisites are absent: Google OAuth client/token, selected pilot folder, Cloudflare hostname/connector, and Docker executable. Requested pilot folder/hostname while independent implementation continues. No account or public-access configuration has been assumed.





## Next-stage prioritization — 2026-10-02

User requested recommendations for the next stage. Reviewed the current working tree and API/UI integration; this checkpoint does not certify the in-progress changes.

Recommended order:
1. Finish and verify the existing reliability/synchronization work: setup UI, persisted Drive sync, thumbnail-cache controls, recovery tests, and a small real-library/phone pilot. Backend additions are present but integration and acceptance remain incomplete.
2. Complete gallery discovery: image-to-image similarity using existing vectors, timeline/date filters, ordinary albums, saved searches, bulk metadata undo, and mobile PWA installation. These remain the existing stage-3 scope, not a new completed phase.
3. After the pilot, consider optional local OCR for searchable screenshots/documents, metadata portability (albums/tags/people labels export/import), and browsing performance measured on a larger library.
4. Evaluate another visual model only if the existing encoder's measured similarity quality is insufficient.

No additional feature implementation was started by this recommendation request. Previously recorded external verification prerequisites remain unresolved.

## Implementation checkpoint — 2026-10-02

Authoritative changes made during the active stages 1–3 goal:

- CPU/GPU dependency profiles and a setup script; startup preserves the selected profile. Compose passes runtime/provider/timeout, healthchecks accept only intended 401 errors. Existing vector collection dimensions/distance are validated, and actual loaded inference providers are reported.
- Added setup/status dialog with model preparation, approximate cache-byte progress, Drive/access readiness, sync controls, thumbnail budget/clear controls and job history.
- Added persisted selected-root Drive change cursors, bootstrap-before-listing checkpoints, transactional per-page publication, directory move/removal handling, pause/resume and capped retry backoff. Overlapping selected roots are rejected to preserve folder ownership.
- Added bounded private Drive JPEG preview cache with revision invalidation; originals remain remote.
- Added date ranges/month timeline with capture/modified distinction, independent albums, persistent saved searches, bulk tags/favorites/album edits and conflict-aware undo.
- Added general-content Find similar using existing vectors and separate near-duplicate candidate comparison/review with content-revision invalidation. No automatic original deletion.
- Added PWA shell/manifest/icons. Service worker caches only interface assets, handles 401 by clearing cache/notifying UI, and does not persist private images/API results for offline use.
- Added checksum-verified backup archive/new-directory restore, optional locked local vector copy and credential exclusion.

Verification so far:
- Existing 44-test suite passed at the initial checkpoint (before new gallery tests).
- Real default SigLIP 2: 768-d image/text inference passed all three RGB ranking checks; unchanged second pass processed zero images. First run exposed Windows cp1252 tokenizer decoding; `start.ps1` now uses `-X utf8`, and a rerun passed.
- Eight new synchronization/cache tests passed: bootstrap race, folder move out/in, per-page failure checkpoints, pause mid-response, unrelated changes, root removal, job backoff/retry, revision/budget/disabled-cache behavior.
- Six organization/date/undo tests passed; ten semantic tests passed including image-vector reuse/filtering and duplicate review invalidation; two backup tests passed including restore and corruption rejection.
- JavaScript syntax checks passed. Temporary browser gallery verified setup status, album/search creation, bulk favorite/album assignment, undo, and source-excluding similar results. Discovery dialog inspected at 390×844 phone viewport.

Still required: complete current full-suite/configuration checks, broader authenticated HTTP/PWA/mobile verification, vector-backup restoration verification, external Qdrant/Docker/CUDA checks where available, real selected Drive pilot and Cloudflare phone acceptance. Do not mark stages 1–3 complete based on these local checks alone.

### Latest verification and remaining gates — 2026-10-02

- Default-model full suite: 70 tests passed before the final job-history/orphan-cache additions. CLIP-configured final suite: 72 tests passed; default final rerun still in progress at this checkpoint.
- Three Node service-worker tests passed: offline shell fallback, no offline API/photo fallback, and 401 cache purge/client notification. Private image responses now use `private, no-store` to avoid implicit browser disk caching. Authentication failure clears visible gallery/viewer data even without an active service worker.
- Real model-switching verifier passed SigLIP 2 → CLIP → SigLIP 2 against one temporary catalog: processed counts 2, 2, 0. Both encoders also passed separate real ranking/resume checks. Verifiers isolate vector storage from any configured production external collection.
- Backup roundtrip now includes embedded vectors; a restored Qdrant collection returned its original vector. Live-store copying is rejected using the actual Qdrant lock. Archive corruption is rejected before the restore directory is created.
- Authenticated HTTP verification covers the new setup/gallery/sync/timeline/duplicate APIs and PWA assets. Unauthenticated requests received 401; date filters/timeline and album creation worked with authentication; status did not expose the password.
- New tests passed for capped repeated change-token rejection, overlapping-root preservation, actual DateTimeOriginal extraction/legacy rescan, meaningful embedding job counts, and cleanup of owned orphan thumbnail files.
- Browser QA verified phone-width duplicate comparison, confirmed decision and undo. Screenshot: `data/mobile-duplicate-review-qa.jpg` (temporary generated fixture images only). Temporary QA server was stopped. Automatic approval review rejected removal of two QA temporary directories with only “blocked by policy”; those directories were left in place.
- Job history now includes catalog scans, embeddings and Drive sync, with processed/failed counts and duration. Interrupted jobs are recovered before startup workers begin.
- Legacy local EXIF DateTime is not treated as capture date. Rescanning reads DateTimeOriginal and refreshes provenance; until then local legacy dates use the modified-date fallback. Drive capture metadata remains supported.

Implementation is present across stages 1–3, but release acceptance is still unproven. Remaining gates:
1. Finish final default-suite observation and review current diff/configuration. Validate live external Qdrant and Docker deployment when available; GPU execution requires its own configured runtime.
2. Real Drive pilot: OAuth client/token, an independent selected root, initial/incremental timing, changed/moved/removed files and reconnect/restart behavior on the actual account. Current Drive state is configured=false, connected=false.
3. Real Cloudflare hostname/connector and phone: authenticated access, slow-network behavior, expired authentication, PWA installation and search-quality acceptance. Docker and cloudflared are unavailable on PATH here.
4. Validate gallery usefulness/near-duplicate quality on the pilot library; fixture ranking and layout tests do not prove personal-photo quality or phone deployment.

The persistent goal remains active; no completion claim has been made.

Final local verification: 72 Python tests passed under the default SigLIP 2 configuration and 72 passed with IMAGE_INDEX_MODEL=clip. Three service-worker tests and JavaScript syntax checks passed. git diff --check reported no whitespace errors. Live deployment/pilot gates above remain open.

## Deployment verification continuation — 2026-10-02

The previous goal turn made implementation/verification progress. Rechecked current code and remaining gates rather than treating earlier plans as completion evidence.

- Found a real packaging gap in optional GPU deployment: the app container requested no NVIDIA device and the base GPU dependencies omitted CUDA/cuDNN runtime packages. Added `docker-compose.cuda.yml` to select GPU build/profile, request one device and default to explicit CUDA. GPU requirements now use ONNX Runtime's matching CUDA/cuDNN extras, and generic provider selection preloads runtime libraries before session creation. Official ONNX documentation confirms the pinned 1.30 GPU wheel uses CUDA 13/cuDNN 9. Provider-selection tests pass and Docker Compose successfully rendered the combined configuration.
- Located Docker Desktop 4.40.0 and its CLI outside PATH. Launching the installed desktop produced a startup failure in its analytics socket (`userAnalyticsOtlpHttp.sock`); the engine remains stopped. No factory reset, data deletion or host driver changes were performed. Actual Docker build/runtime acceptance remains open.
- Ubuntu WSL is available. Downloaded official Qdrant 1.19.1 Linux release and verified SHA-256 `eef986e769d4d3e806dd2d546e1b4ecdd416211e54d34b4ed764fac7c58e1085`. Ran an isolated localhost server with separate temporary storage and telemetry disabled.
- Live external-Qdrant acceptance passed for both real encoders (SigLIP 2 768-d and CLIP 512-d): writes/ranking, album/favorite SQL filters, image-vector reuse, unchanged scan resume, reopened client state and missing-point cleanup. Added `verify_qdrant_server.py` with unique temporary collections and optional real-model inference; it does not write to personal image collections.
- Restarted the actual Qdrant process and verified a persisted fixture vector/payload/ranking survived. Removed the isolated test collection, confirmed the server had no collections remaining, then stopped the test server. External CPU Qdrant server behavior is now verified locally; Docker-packaged Qdrant and GPU indexing remain separate unverified deployment modes.
- NVIDIA GTX 1650/driver 591.86 are present. Project .venv still advertises Azure/CPU only. GPU dependency dry run is active (tool session 53469), downloading wheels into pip cache without installing them. Created an isolated verification environment under ignored data and added `verify_gpu_runtime.py` to install/test CUDA there without replacing the user's CPU environment. Actual GPU inference is not yet proven.
- Requested the real pilot Drive folder, Cloudflare hostname and existing OAuth-client path while continuing independent work. Those live account/device gates remain open.

Next actions: observe the existing GPU dependency resolution handle; install into the isolated environment only once resolution finishes, verify actual CUDA image/text sessions, then finish Docker startup/build verification if safely recoverable. Keep real Drive/phone prerequisites pending; synthetic/server fixture success does not prove personal-library search quality.

### Additional acceptance evidence — 2026-10-02

- Authentication race fixed: a 401 locks the current page, invalidates earlier API responses and clears visible private catalog/viewer/organization data. A later online event cannot hide the sign-in notice, and locked requests do not fetch until reload. Two tests execute the actual API/PWA code with an out-of-order response; both passed. Together with the service-worker privacy tests, five Node tests pass.
- Executed the actual Compose healthcheck script against a real temporary HTTP server: 200/401 are healthy, while 404/500/503 fail. New packaging test passed. JavaScript syntax/verification-script compilation and whitespace checks passed.
- Qdrant process restart durability is complete as recorded above. The verification server is stopped and had no remaining fixture collections. Docker Desktop startup still fails on its existing analytics socket, so an actual container build has not been claimed.
- GPU dependency dry run (session 53469) remains a verified live process. CUDA/cuDNN, FFT, random-number and BLAS runtime wheels have downloaded; NVJitLink was downloading at the latest observation. No GPU packages have been installed into the project's .venv. Continue observing this same handle, then run the isolated GPU verifier once resolution completes.

### CUDA verification completed — 2026-10-02

- Dependency resolution finished successfully. Installed the GPU profile only into ignored `data/gpu-verification-env`; the application's CPU `.venv` remains intact. The isolated runtime passed `pip check`.
- `verify_gpu_runtime.py --prepare` completed with exit code 0. Both SigLIP 2 (768 dimensions) and CLIP (512 dimensions) loaded actual CUDA sessions on the GTX 1650 with explicit CUDA enforcement. Each model ranked the matching red/blue/green fixture first and processed zero images on an unchanged second indexing pass.
- ONNX Runtime emitted a CLIP graph memory-copy performance warning; ranking and resume checks passed. This fixture test does not establish throughput or quality on the personal library.
- External Qdrant server persistence and CUDA inference now have actual local acceptance evidence. Docker Desktop still cannot report a running engine; container build/runtime acceptance remains open. No Docker reset or existing-data deletion was performed.
- Final regression audit passed: 73 Python tests under default SigLIP 2 and 73 under CLIP, plus all five Node authentication/service-worker tests. `git diff --check` passed with only repository line-ending notices. No verification processes remain running.
- Next: obtain the OAuth-client path/Drive pilot root and Cloudflare hostname, verify real incremental sync and library search quality, then complete phone/tunnel acceptance. Those prerequisites remain pending; stages 1–3 are not fully accepted.

## Stage 4 — Optional local OCR — 2026-10-02

User authorized the next phase and selected English first. Implementing the roadmap's optional text search; the Drive/Cloudflare/Docker acceptance gates remain independent and open. DINOv3 remains deferred until personal-library measurements justify another visual model.

Plan:
- [x] Verify runtime/model distribution, license and fixed model checksums. RapidOCR 1.4.4 ships PP-OCRv4 models; Apache-2.0 licenses from RapidOCR/PaddleOCR. The 14.9 MB wheel SHA-256 matches PyPI's published digest. Three bundled model hashes are verified on every worker invocation.
- [x] Isolate OCR dependencies in `data/ocr-env`, preserving CPU/GPU embedding environments. Pin primary dependencies; add setup command and runtime availability status.
- [x] Add opt-in single-worker automatic extraction, on-demand photo extraction, pause/retry/clear and startup resume. Input uses the existing 64 MB checksum/orientation-safe loader, then a 1600px in-memory PNG; 60-second subprocess limit, two CPU threads. Originals are never saved or edited.
- [x] Persist revision/model-scoped text, errors and timings in SQLite; stale/missing/model-incompatible results do not appear in search or the viewer. Epoch guards discard in-flight results after pause/clear. Record jobs in shared job history.
- [x] Add authenticated OCR API, combined names/tags/OCR literal search and OCR-only mode, filtered through existing folder/person/album/date/favorite options. Keep visual ranking unchanged.
- [x] Add setup controls and selectable viewer text with mobile touch targets; clear visible OCR text on authentication failure. Update shell cache version.
- [x] Finish failure/recovery/API regression checks, real English fixture cost measurement, and local mobile UI verification.
- [x] Document installation, text privacy, backup/clear semantics and measured limitations.

Early evidence: isolated runtime installation and pip check passed; model checksums verified. Real generated English invoice fixture extracted `INVOICE ACME2026 / Payment received`, found by catalog text search, in 2.11 seconds including process/model startup. Initial service tests found an incorrect test Basic-auth username (the app expects `frame`); corrected the fixture, pending rerun. No personal images were processed; automatic extraction remains disabled.

Final verification: 80 Python tests passed under default SigLIP 2 and 80 under CLIP, including seven OCR tests for authenticated/filtered search, persistence, changed/missing content, clear during extraction, explicit retry, queued photo revision validation, and restart/job-count behavior. Five Node privacy/authentication tests passed. Main CPU environment `pip check`, JavaScript syntax, Python compilation and whitespace checks passed. Real extraction was rerun with assertions that all three actual ONNX sessions use CPU, passing in 2.61 seconds. A wrapper-attribute mismatch in the new provider assertion was corrected using the actual RapidOCR recognizer session structure before this successful check.

Browser verification at 390×844: single-photo extraction produced `INVOICE ACME 2026`; extracted text displayed in the viewer, the extraction button measured 44px, and document width matched the viewport. OCR-only matching and empty searches, setup progress/job history, enable and pause controls were exercised against generated fixtures in ignored `data/ocr-ui-qa`, independent of the personal catalog. Screenshot: `data/mobile-ocr-qa.png`. Real phone/Cloudflare and personal-library OCR accuracy remain unverified. Automatic extraction is disabled in the personal catalog; installed runtime is ready to opt in through Setup after app restart.

Handoff: 320px gallery verification also showed no horizontal overflow. The temporary browser tab was closed, viewport reset, and fixture server stopped. After refining job accounting to report discarded in-flight work as interrupted rather than failed, all seven OCR tests and five Node tests passed again. Assistant pointer files remain correct. Restart the dashboard to load the new routes/UI, then opt in to OCR through Setup or extract individual photos. No personal-library OCR pass was started. Stage-4 OCR implementation/local verification is delivered; earlier Drive/Docker/Cloudflare acceptance gates remain open.

## Commit checkpoint — 2026-10-02

User requested committing the current changes. Included reliability/runtime setup, incremental sync/private thumbnail cache, gallery discovery/PWA, backup/verification tooling and optional OCR with their tests and documentation. Runtime data, OAuth secrets, environments, model weights and QA artifacts remain ignored. Verification evidence and outstanding Drive/Docker/Cloudflare acceptance gates above remain unchanged. This is a local Git commit; no push or deployment is requested.
