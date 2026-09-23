# Personal image index — plan and progress

Last updated: 2026-09-23 (Asia/Calcutta)

## Working agreement

Keep this file updated whenever the plan, progress, verification results, or remaining work changes. The application now lives in this `imadex` directory (previously `index`). Preserve other ongoing edits.

## Objective and decisions

- Original images remain in Google Drive; the dashboard runs on this PC.
- Phone access uses Cloudflare Tunnel with authenticated access.
- Image-content embeddings and natural-language visual search are core functionality.
- Qdrant local mode persists vectors; SQLite stores metadata and embedding job state.
- Paired CLIP ViT-B/32 image/text models run through FastEmbed/ONNX Runtime on the CPU, producing normalized 512-dimensional vectors ranked by cosine similarity.

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

## Limits and future work

- CPU inference; no external inference API. Short English descriptions work best.
- No OCR, face identification, or automatic tagging.
- 64 MB embedding-input limit; unsupported formats are reported as failures. Animated/multipage images use the first frame.
- Embedded Qdrant targets a personal library; larger collections may need a dedicated Qdrant server.
- Folder scans are manual; each scan automatically queues new/changed embeddings.

## People gallery discussion — 2026-09-23

- Rechecked the live `/api/embeddings` endpoint: Qdrant local, CLIP 512 dimensions, no error; total/ready/pending/failed are all zero. Implementation is ready, but the personal Drive library still needs connection and its first scan.
- User requested an iOS-style People gallery. No face functionality has been implemented.
- Available next scope: local face detection, manual person labels, and mobile People albums with rename/remove controls. Automatic identity matching and a facial-recognition database are outside the implementation offered here.
- Keep originals in Drive and any manual labels in the local catalog. This proposed addition has not been started.

## People gallery implementation plan

Status: implementation in progress. Manual-label service and API integration are written; verification is pending. User requested implementation on 2026-09-23. Unchecked items below remain unverified.

### Scope and expected behavior

- Add a People section containing albums assembled from explicit user labels.
- A photo can contain multiple people. The user selects existing names or creates new ones; no person is inferred from appearance.
- Optional local face detection can highlight regions that may contain faces to help the user review a photo. Detection does not assign or suggest names.
- A new photo starts without person labels. The user can label multiple deliberately selected photos together to reduce repeated work.
- Keep the existing CLIP visual-search pipeline and Qdrant collection intact. People filtering uses manual SQLite relationships, not CLIP similarity.
- No face identity embeddings, automatic cross-photo matching, or recognition-database work is included in this plan.

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
