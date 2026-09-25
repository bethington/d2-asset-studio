# Meshy WEB API (internal) — reverse-engineered for free-retry generation

Meshy runs **two separate auth systems**. The documented **openapi** key (`Bearer msy_…`) hits
`api.meshy.ai/openapi/v1/*`, is billed per call, and has **no free-retry**. The **web app** uses a
**browser login session** (a Supabase JWT, ~1h expiry) sent as `Authorization: Bearer <jwt>` to the
internal `api.meshy.ai/web/*` API — this is where the plan's free **×8 retries** live. The openapi
key is rejected (401) on `/web`. Tier confirmed **studio** (8 free retries) via `GET /web/v1/me/tier`.

## Getting the session token (no manual paste)

Launch the user's browser (Brave/Chrome) with `--remote-debugging-port=<p> --remote-allow-origins=*`
on their real profile (login persists). The JWT is **not** in localStorage/cookies — it's held in
memory and attached as a header. Capture it via CDP **Network**: connect to a `meshy.ai` page target,
`Network.enable`, and read the `Authorization: Bearer …` header off any `api.meshy.ai/web` request
(trigger one with `fetch('/web/v1/me/tier',{credentials:'include'})`). Re-read on demand (expires ~1h).
Gotcha: CDP `Target.setAutoAttach` only catches NEW tabs — connect to each existing page target
directly. The app uses **axios over XHR**, so a `window.fetch` hook misses calls — use CDP Network.

## Endpoints (all `https://api.meshy.ai/web`, `Bearer <jwt>`, `Origin: https://app.meshy.ai`)

### Register an input image  ✅ working
`POST /v1/files/images` — multipart form field `file` = PNG bytes; query `?removeBackground=false`.
→ `{result:{id, url, name}}`. The `id` (e.g. `86ac…f90feacf.png`) is what the create references.
(Meshy even auto-names it, e.g. "Armored Dome".) IMPORTANT: prep the sprite **aspect-preserved**
(pad to square) — a `resize((512,512))` distorts non-square sprites and Meshy builds the distortion.

### Create the DRAFT (geometry, untextured)  ✅ working — costs ~20 credits
```
POST /v2/tasks
{ "phase":"draft", "batchId":"<uuid>",
  "args":{"draft":{ "aiModel":"avocado" (=Meshy6), "modelType":"standard",
    "topology":"triangle", "imageIds":["<image id>"], "shouldTransferImageStyle":true,
    "symmetryMode":0, "seed":0, "license":"private", "prompt":"" }} }
→ {"result":"<draftTaskId>"}
```
Poll `GET /v2/tasks/{id}` → `{status, phase, mode:"draft", result:{...}}`. status SUCCEEDED ≈ 40s.

### TEXTURE the approved draft  ✅ captured live
```
POST /v2/tasks
{ "phase":"texture", "parent":"<draftTaskId>",
  "args":{"texture":{ "imageId":"<image id>", "artStyle":"realistic", "aiModel":"avocado",
    "enablePBR":true, "srMode":"weak", "textureSize":0, "prompt":"" }} }
→ {"result":"<textureTaskId>"}
```

### Free ×8 RE-ROLL of a draft  ✅ captured live (2026-07-19)
```
POST /v2/tasks/{taskId}/retry     (EMPTY body)  → 200
```
Captured by CDP-driving the workspace viewer's ⟳ ×8 on draft `019f7901…` (the UI shows a
"Confirm retry? The current version will be replaced" dialog first). Semantics, all verified:
- **In-place REPLACE with a new id**: a NEW task appears (same name/params, `retryCount`+1,
  status IN_PROGRESS) and the OLD id starts **404ing** immediately — always adopt the new id.
- **Free**: main credit balance unchanged (4,928 → 4,928). Remaining free re-rolls per draft
  = `8 - retryCount` (the grid's ×8/×7/×6 badges are exactly this).
- Only shown for UNTEXTURED drafts (the ×8 button vanishes once a draft is textured).
- Earlier guess of PATCH was wrong — the preflight `OPTIONS /v2/tasks/{id}` belonged to this
  POST on the `/retry` subpath.
Wired: `meshy_web.retry_task()` → `/api/studio/reroll` now uses this (returns `free: true` and
the NEW task id; the old parent-linked-draft fallback that cost ~20cr is gone).
Historical dead-ends kept for the record: `parent`-linked draft create is NOT free (cost 20cr);
`POST /v2/tasks/{id}/regenerate` (retryTaskV2) is failed-tasks-only.

### Other
- `GET /v1/me/tier` → `{tier:"studio", freeMonthlyCredits, …}`.
- `GET /v2/tasks?pageNum=&pageSize=` → task list; `GET /v2/tasks/{id}` → full task.
- `GET /v1/files/upload-url-for-public {prefixes:["image"], filename}` → presigned S3 PUT (alt upload
  path; the CDN url is `https://cdn.meshy.ai/{key}`) — but the create wants the **registered** image
  id from `/v1/files/images`, not a raw S3 key ("Image not found").
- Task `mode`: `api-image-to-3d` (from the openapi key/API console) = **not retryable**; `draft`/
  `generate` (from the web workspace) = retryable. `canRegenerate = !isExample && mode!="upload" &&
  phase ∈ {draft,texture,image-to-3d-texture,generate}`.

## Two-phase flow (what the studio implements)
1. register image (aspect-preserved) → 2. create **draft** → poll → **3D preview** →
3. re-roll ×8 (**free** — `POST /v2/tasks/{id}/retry`, adopt the new id) until the shape is
right → 4. **texture** the chosen draft → poll → preview textured → 5. accept → download GLB →
Blender render → DC6 → push to game.

## Pairing existing workspace tasks with catalog items (2026-07-19)

`app/meshy_links.py` + `/api/meshy/*`. Links persist in `<workspace>/meshy_links.json`
(`{task_id: {item_id, image_id, phase, source, name, linked_at}}`); the server seeds its
in-memory `_STUDIO` map from it at boot, so pairings survive restarts (previously they
died with the process).

**There is no server-side image listing** — `POST /web/v1/files/images` is upload-only and
every `GET` variant 404s — so a task's origin item can't be recovered by filename. Pairing
uses the task's **input image** (`args.draft.imageUrl`) instead:
- **dHash (64-bit) vs each catalog sprite** run through the same `prep_image_for_meshy`
  used at upload. Studio-created tasks re-hash to **d=0**.
- **Name similarity** as a weak secondary — unreliable alone because Meshy auto-names by
  *appearance*: the Plate Mail sprite came back named "Chainmail hauberk".

**Threshold calibration (measured, not guessed).** A real scan of 60 tasks × 1406 items was
rendered as a side-by-side contact sheet: all `d==0` pairs correct; `d` in 5..8 mostly WRONG
(gauntlet↔skeleton key at d8, glove↔potion at d7, leather↔metal boots at d8). Small dark
sprites with similar silhouettes collide. So **auto-link only at d≤2**; d≤16 and name hits
become one-click suggestions shown next to the input thumbnail. Result on the live
workspace: 3 auto (all correct), 24 suggestions, 5 unmatched — the unmatched/loose ones are
mostly hand-uploaded hi-res renders that were never catalog sprites.

First scan hashes the whole catalog (~16 min, mostly DC6 decode) and caches to
`item_dhash_cache.json`; later scans take ~10s.

### Pairing review page (`/pairing`, 2026-07-19)

Filmstrip rows: the task's **input image + generated preview** pinned left, candidate item
sprites as radio cards right, ordered strongest→weakest with a reason badge (`image d12`,
`name 59%`). Radio + "Link this" per row, or "Link all picked" for a batch
(`POST /api/meshy/links/batch`).

Two hard-won requirements, both from looking at real data:
1. **Per-row "search all items"** — the auto-candidates frequently contain NO correct
   answer, because much of the workspace is hand-uploaded hi-res art that was never a
   catalog sprite. dHash then returns five near-identical wrong sprites (a gauntlet
   upload offered five skull/key icons at d6). The search box is the escape hatch and
   is the primary path for those tasks.
2. **Low-confidence links are re-offered, not hidden.** `is_high_confidence()` gates on
   the link's `source`: only `auto-image d≤2`, `studio-*`, `manual*`, `reviewed*` are
   trusted. Anything else (notably legacy `fuzzy-confirmed` picks made through the old
   dropdown, before candidates showed sprites) comes back with a red "was linked to X —
   confirm or change" badge and the current pick offered first. Without this, a blind
   guess silently became permanent and vanished from review (21 such links existed; one
   had put a scale-armor generation on `herb`).

### Candidates are DC6 ART FILES, not items (2026-07-19 correction)

The first cut ranked per catalog ITEM. Wrong model, two ways:
- **Arbitrary label.** 1406 items resolve to only **577 distinct DC6s**. `invtow.dc6` is
  Tower Shield *and* Pavise *and* Aegis *and* Sigon's Guard, so an exact d0 match on that
  file was reported as whichever item happened to sort first ("Tower Shield") when the
  user knows the same art as the Aegis.
- **Wasted slots.** `invne4.dc6` backs Gargoyle Head, Cantor Trophy, Succubae Skull,
  Trang-Oul's Wing and Boneflame — so a 5-candidate list could show the SAME picture five
  times. That is what made the suggestions look useless.

The art file is also the true unit of work: an override replaces `invtow.dc6` for
everything that uses it. So `dc6_hashes()` groups items by `invfile`, hashes each file
once, and ranks files. A candidate carries `{invfile, item_id (representative), items[],
item_count}`; the card shows the sprite, `invXXX.dc6`, the match reason, and "N items:
…". `_rep_item()` picks the canonical owner (base-category, then shortest name) for the
representative that the Studio needs for cell dims + activation.

Side effects: the scan decodes 577 files instead of 1406 items (265s vs 991s cold), and
`item_dhash_cache.json` is now keyed by invfile. Links carry an `invfile` field; existing
links were migrated in place. `SUGGEST_MAX_DISTANCE=16` is deliberately permissive since
real hits land as far out as d13 (the scale-armor upload matched Templar Coat there) —
tighten it if the tail feels noisy.

### The re-imagined art library is the pairing key (2026-07-19, decisive)

The reference images fed to Meshy are NOT the DC6 sprites — they are AI-redrawn versions
of them (`D:\d2\DC6\Data\global\items`, override with `PD2_REIMAGINED_ART`). The redraw
keeps the subject and none of the pixels, and even recomposes: the game's paired-gauntlet
`invlgl.dc6` was split into single left/right hands. So no pixel, silhouette or shape
metric can connect a hand-made generation to its DC6 — measured, not assumed.

What makes it exact is that the library is **named by DC6 file**: `armors/invplt.png`,
`gloves/invtgl-l4.png`, `boots/invhbt-1.png`. Hash the library, match a task's input
image against it, and the filename names the DC6 outright. `dc6_name_from_art_path()`
strips the `-L/-R/-l3/-lj4` variant suffixes (split hands / numbered redraws) and
validates the result against the known DC6 set so a strip can never invent a file.

Matching order: re-imagined library first (hand-made tasks), then the DC6 sprites
themselves (Studio-created tasks upload those directly). Both hit at d0.

Result on the live workspace: **3 auto-pairs → 23**, all distance 0, e.g. `invbrnz.dc6`
(brain) ↔ "Cerebral Nut", `invcar.dc6` (orbital globe) ↔ "Golden Orbital Globe Emblem",
`invhbt.dc6` (plate boots) ↔ "Greaves of the Golden Sentinel". 909 PNGs cover 509 of the
577 DC6s; the ~200 unresolved names are PD2 customs (`invch1`, `invbonr2`) no catalog
item references — still valid DC6 targets, which is why `/api/dc6/<name>.png` renders by
file name rather than through an item.

The page (`/pairing`) is anchored on art files: LEFT the original DC6 artwork, RIGHT the
generations matched to it. Several generations can target one file (four re-imagined
variants of `invtgl`), so a radio picks which one that file uses (`POST
/api/meshy/primary`, exclusive per invfile). Generations with no library match land in an
"Unplaced" strip with per-item search.

### "None" — declining a pairing (2026-07-19)

Three levels, because a wrong match must FREE the generation rather than strand it:
- **Per art file** — the `none` card in a row (`POST /api/meshy/none {invfile}`) unlinks
  every generation on that DC6; they return to Unplaced, reassignable.
- **Per generation** — the `✕` on a card (`DELETE /api/meshy/links/<task_id>`) frees just
  that one, leaving the rest of the row intact.
- **Never pair this** — `none` on an Unplaced card (`POST /api/meshy/ignore`) for
  generations that aren't game art at all. Stored as an `ignored` link entry rather than
  deleted, and `auto_pair()` skips ignored tasks, so **a rescan cannot silently re-link
  it**. Reversible via the "Marked none" strip.

Verified round-trip: none on `invbsc` → 14→13 pairs, generation back in Unplaced; ignore
→ survives a full rescan unlinked; restore → 14 pairs again.

### Glove pair compositor (2026-07-19)

D2 draws gloves as a PAIR in one sprite, but the re-imagined library splits them into
single hands (`invtgl-l4.png` / `invtgl-r4.png`) because one hand is what Meshy can
usefully model. `app/glove_pairs.py` puts them back together.

**The hand is known, not guessed** — `hand_of()` reads it off the matched library
filename (`-l4` left, `-rj2` right; the `j` series is a second set of redraws). Boots
are numbered (`invhbt-1`) with no `-l`/`-r` and their reference art is already a pair,
so they are correctly not pairable.

**Why a hand-tuned template.** Connected-component analysis of all five glove DC6s
returns exactly ONE region each — the two gloves overlap, so there is no way to split
the original and learn each hand's position from it. Instead each art file stores a
layout (`pair_templates.json`), tuned once against the original shown as a ghost and
reused by every variant pair of that glove (l1+r1, l4+r4, lj2+rj2 …). Placement is
normalised (`cx`/`cy`/`scale` as fractions, `rot` degrees) so it survives any change of
resolution.

**Always a pair.** A hand with no generation is mirrored from the other, flagged in the
row and in the tuner (which previews it mirrored, so you never position a picture the
build won't produce). It upgrades automatically once the real second hand is generated.

**Output sizing.** The composite is built at the ORIGINAL sprite's pixel size (invtgl is
56x56, not the 2*29=58 the cell grid implies) and encoded by `canvas_to_dc6()` —
deliberately NOT `assets.png_to_item_dc6()`, which crops to the alpha bbox and re-fills
the cell, undoing the placement.

Routes: `/api/pair/template/<invfile>` (GET/POST), `/api/pair/ghost/<invfile>.png`,
`/api/pair/hand/<task>.png` (source art, flat background cut), `/api/pair/preview`,
`/api/pair/build`. Build renders each hand's GLB in Blender at the inventory angle,
composites, saves the alternate and activates it — so Push to game is unchanged.

Verified live: `invlgl` built from two genuine 3D models in 23s to a valid 56x56 DC6
pair; `invtgl` (left hands only) built with the right mirrored.

### The number is the VARIANT, and it pairs the hands (2026-07-19)

`invtgl-l4` / `invtgl-r4` are not merely "a left and a right" — they are **one redraw's
two hands**, and the trailing number is what says so. `-lj2` / `-rj2` likewise for the
`j` series; `-L` / `-R` are the unnumbered base pair.

This matters because selecting hands independently silently mixes designs. `invvgl` has
`l3`, `l5` and `r5`: a first-left + first-right pick pairs **l3's left with r5's right**
— two different gloves on one pair of hands, and nothing in the output announces it.

So the VARIANT is the unit of selection. `variant_of()` parses it, the server groups a
row's generations into variant sets (`{variant, left, right, complete}`) sorted
complete-first, and the row offers one "pair set" dropdown instead of two hand
dropdowns. The default lands on a complete set so nothing is mirrored unnecessarily.
A set missing a hand mirrors the other and names the exact file to generate
(`invtgl-r4`) to make it real.

Live grouping: invlgl 2 of 2 sets complete; invvgl 1 of 2 (variant 5 complete, 3 is
left-only); invtgl 0 of 4 and invmgl 0 of 2 — left hands only, all mirroring for now.

### Output bounds in the tuner (2026-07-19)

The composite canvas is fixed at the ORIGINAL sprite's size (56x56 for gloves), so
anything positioned outside it is discarded at build time. The tuner previously let a
hand be dragged or scaled anywhere with no indication, so work could be silently thrown
away.

The stage now draws the real bounds: a gold frame at the exact output rectangle, faint
cell-grid lines (2x2 for gloves), and everything outside dimmed. A live badge reads
`fits` / `8% clipped` / `29% clipped`, coloured green-amber-red, measured per frame from
each hand's axis-aligned box against the canvas (rotation is approximated). `out_size`
and `cells` come from the server so the frame is the truth, not a guess.

Clipping is NOT prevented — the original glove art genuinely runs to the edges
(`invtgl`'s content bbox is 1,0..56,55), so bleeding off the edge is legitimate. A
`fit inside bounds` button shrinks whatever overflows until both hands sit inside, for
when it wasn't intended.

Implementation note: the dim ring is a bounded box-shadow inside an `overflow:hidden`
wrapper. An unbounded spread also washed out the sliders and buttons.

### Hard containment: no pixel can leave the sprite (2026-07-19)

The tuner now makes it impossible to position, scale or rotate a hand so any opaque
pixel falls outside the output rectangle. Dragging hard-stops at the border; scale and
rotation nudge the hand inward instead of jamming, so a slider still reaches the true
maximum without manual repositioning.

Clamping is against the **silhouette**, not the bounding box. A glove rotated 35 degrees
has large transparent corners in its box; clamping the box would hold the artwork away
from the border and stop it filling the sprite the way the original does. The server
samples each hand's opaque outline once (`silhouette_points()`, per-column top/bottom,
48 samples, cached) and serves it via `/api/pair/outline/<task>`; the client rotates
those points per frame -- far cheaper than testing thousands of pixels while dragging.

Two bugs this surfaced, both real:
- **Preview/build size mismatch.** `place_hand()` scales so the LONGEST side equals
  `BASE_FIT * scale * W`, but the CSS was setting WIDTH to that. For a tall glove
  (aspect 0.8) the preview drew it ~25% too large, so what you arranged was never what
  the build produced. Both now size by longest side.
- **A 1px leak.** With exact math, 3 of 1587 opaque pixels still escaped: bicubic
  rotation feathers alpha slightly beyond the sampled outline and placement rounds to
  whole pixels. A one-output-pixel inset (`eps = 1/out_width`) makes the guarantee
  literally true.

Verified independently of the browser's own math: the live clamped template was replayed
through the compositor onto a padded canvas and every opaque pixel counted --
**1528 of 1528 inside, 0 outside** after abusing scale to 3x, rotation to 35 degrees and
dragging 9000px past the corner.

### Glove hand detection + mirroring: measured findings (2026-07-19)

**Every `-r` file is the `-l` file flipped.** All 34 complete l/r pairs in the library
compare pixel-identical after a horizontal flip (silhouette IoU 1.000, mean pixel
difference 0.0). So generating both hands in Meshy is wasted effort — one generation per
variant is enough and the opposite hand is exact, not an approximation. The compositor's
mirror fallback therefore produces byte-identical output to using the real `-r` art.

**Pinky-edge detection alone is NOT reliable enough to replace the filename.** Across all
68 single-hand files it agrees with the filename 60/68 (88%). Every failure is a
symmetric mirror pair — `invtgl-l7/r7`, `lj2/rj2`, `lj3/rj3`, `invvgl-l4/r4` — at a
margin of only 0.05–0.10, on designs like the clawed `invtgl` whose cuff makes both edges
similarly straight. The symmetry proves the filenames are self-consistent and the
detector is what flips.

Those eight are genuinely oriented opposite to the other sixty: `invtgl-l4` has its cuff
upper-right with claws down-left, while `invtgl-l7` is its mirror, yet both are `-l`. A
mirror is not a rotation, so they are opposite hands under the same label.

**This makes the two signals complementary rather than competing.** The filename gives
INTENT (which hand this is meant to be) and detection gives ORIENTATION (which way the
art actually faces). When they disagree the fix is simply to flip the art — which is
free, lossless, and produces the true opposite hand. So mis-oriented files self-correct
instead of needing to be flagged or re-exported.

`analyse_hand_art()` returns both readings plus `margin` and `suspect_mirrored`
(disagreement at a decisive margin ≥0.15 — currently zero files, so no false alarms).

### Glove auto-fit (2026-07-19)

Stands each hand's pinky edge parallel to its border, fills the height, and snaps to the
side — left hand right, right hand left. It is the DEFAULT layout for any glove with no
saved template (`_effective_template()`, used by both the pairs view and the build), so a
glove you never open still builds correctly; a saved template always wins, and the
`✦ Auto-fit` button re-applies it.

**Scale is measured, not predicted.** Resizing then rotating does not scale the alpha
bbox linearly (resampling + rounding), and a closed-form estimate left a 7% overshoot —
which the BUILD would have clipped silently, since only the browser runs the clamp. So
`autofit_hand()` simulates the real `place_hand()` pipeline at a trial scale, measures the
placed silhouette, and iterates to the target. Every glove now lands at exactly 54px in a
56px canvas, and snapping uses the measured width so the outer edge sits one pixel inside
the border.

**Rotation direction.** `tilt` is dx-per-dy: +27 means the edge leans right as it
descends, so it needs a CLOCKWISE rotation to stand up. `place_hand` applies
`rotate(-rot)` and PIL rotates counter-clockwise, so `rot` carries the tilt's own sign.
Negating it rotated to twice the tilt instead of to zero — the glove came out more
diagonal, over-scaled and cropped.

**Mis-oriented art self-corrects.** When detection disagrees with the filename the art is
flipped rather than flagged (lossless, and yields the true opposite hand). The stored
`flip` is XOR-compensated against the compositor's own fallback mirror for a borrowed
hand — without that, a left-only glove came out as two identical hands instead of a
mirrored pair.

Results are cached per (left art, right art, size); first compute ~8s, thereafter instant.

### Two clamp bugs that bunched the gloves in the middle (2026-07-19)

Reported as "I can't slide the glove to the edge, something invisible is blocking it,
and the pair overlaps almost completely". It was not the art — the alpha cutout is clean
(zero faint pixels; bbox identical at alpha>0, >8 and >128 on every glove tested). Two
clamp bugs:

1. **The clamp ran against a bounding-box guess.** Silhouette outlines are fetched
   asynchronously; until they arrived `extent()` fell back to the hand's bounding box,
   which for a 42-degree-rotated glove is far larger than the glove. The clamp fired on
   that and mutated `dx`/`scale` IN PLACE, so the auto-fit was destroyed before it was
   ever displayed (invtgl: dx 0.41 -> 0.17, scale 1.54 -> 1.45) and a later layout with
   the real outline could not undo it. Fixed: `enforce()` is a no-op until the outlines
   load, and the authored layout is re-applied once they do.

2. **A mirrored hand was clamped against an un-mirrored silhouette.** CSS applies
   `scaleX(-1)` before the rotation, so the drawn shape is mirrored, but `extent()`
   rotated the original points. That made the clamp asymmetric and held the right hand
   about 0.2 short of its border (invtgl -0.18 instead of -0.41). Fixed by mirroring the
   outline points first.

After both: invtgl/invmgl/invvgl sit flush and symmetric at their measured auto-fit
offsets, invlgl within ~0.045 (the polygon extent is marginally conservative versus the
server's rendered measurement, which errs safely).

Residual, by geometry not bug: a glove wide enough that two of them at full height cannot
both fit will still overlap. invtgl now has NO overlap; invlgl overlaps ~23% because its
gloves are wide. Zero overlap for those means scaling below a full-height fill.

### Flush snap + locked vertical centring (2026-07-19)

**Vertical is pinned.** A glove is always vertically centred in the sprite: dragging moves
a hand horizontally only, and the offset readout says "vertically centred" rather than
showing a number that never changes. With a full-height fill there is nothing to gain
vertically, and it cannot be knocked askew by a stray drag.

**Auto-fit sits truly flush**, with the outermost cuff pixel ON the border column rather
than one pixel inside it. This is safe specifically because auto-fit measures instead of
predicting: the scale comes from `placed_bbox()` (the real render, fringe included) and
the offset is then corrected by actually compositing the hand and reading back where its
silhouette landed. Two passes settle the integer rounding in the paste.

That correction step was necessary, not belt-and-braces: `place_hand()` centres the whole
ROTATED IMAGE, whose alpha bbox is not centred inside it (rotation pads asymmetrically),
so computing the offset from silhouette width alone still left a 1px gap on every glove.

Measured after: left/right padding 0px on all four gloves, vertical top/bottom equal.
Manual dragging keeps its 1px of slack, since it estimates from the outline polygon and
would otherwise shave faint fringe pixels.

Overlap is now purely geometric: invtgl 0px, invlgl 8px, invvgl 8px, invmgl 10px --
gloves wide enough that two at full height cannot both fit. Scaling below a full-height
fill is the only way to remove it.

### Vertical fill solved exactly (2026-07-19)

Auto-fit scales each glove to the LARGEST size whose rendered height still fits the
canvas. The achievable heights are DISCRETE — `place_hand()` rounds the pixel size to an
integer, and for a heavily rotated glove one extra source pixel can add ~3px of rotated
height — so a multiplicative solver oscillated between two of them and settled low
(invtgl filled 54 of 56 when a better value existed). It now binary-searches the integer
pixel size and takes the biggest that fits.

Result: invlgl, invmgl and invvgl fill 56/56 exactly. invtgl fills 54/56, and that IS its
maximum — the next size up renders 57px and would clip:

    size 45px -> height 54  (chosen)
    size 46px -> height 57  would clip
    size 47px -> height 58  would clip

So the 1px band above and below invtgl is rotation granularity, not slack.

### Two causes of the "padding" you can see but the numbers deny (2026-07-19)

Reported as: auto-fit leaves a gap at the top and on both sides, the cuff never touches
the border. Server-side `getbbox()` said the composite was flush, so the numbers and the
screen disagreed. Both causes were real.

1. **Template fitted from a different variant than the one displayed.** `_pair_art_paths`
   took the first LINKED art per hand while the UI's dropdown defaults to the first
   COMPLETE variant. For invlgl that meant the layout was measured from `invlgl-l3` and
   then used to place `invlgl-l2` — a different silhouette, so it sat off the border
   (dx 0.3654/rot 27.14 instead of 0.3921/27.22). The pairs view now fits the same
   variant the UI selects, and the tuner re-fits for whatever variant is on screen.

2. **The snap was aligning INVISIBLE pixels.** Rotation feathers a 1–2px fringe of
   alpha 1..8 around the glove. `getbbox()` counts any alpha > 0, so the fringe was being
   snapped to the border and the visible cuff sat ~2px inside it — flush by measurement,
   padded to the eye. Auto-fit now measures `visible_bbox()` (alpha >= 16, below which a
   pixel reads as nothing once quantised to the palette) for both the height solve and
   the flush/centre correction.

Measured after: visible padding 0 on all four gloves (invtgl 1px at the bottom and invvgl
1px at the top, from rotation granularity). Coverage rose accordingly — invlgl 70% -> 78%,
invtgl 46% -> 51%.

### Contact shadow for depth (2026-07-19)

Two same-coloured gloves placed side by side read as one merged shape where they touch.
The original sprites solve this with a dark interior seam — measured on 58–87% of their
rows — plus a tonal difference between the hands.

`composite()` now renders each hand to its own layer, grows and blurs the FRONT hand's
silhouette, subtracts the front hand itself, and multiplies the result onto the BACK hand
before flattening. So the back glove is darkened only where the front one looms over or
abuts it, fading with distance; both gloves keep their own colour everywhere else.

Constants at the top of the module: `SHADOW_SPREAD_PX` 2.0 (at 56px, scaled with the
canvas), `SHADOW_STRENGTH` 0.62, `SHADOW_FEATHER` 1.2. `composite(..., contact_shadow=False)`
disables it.

Note that auto-fit separates the hands to the borders, so the only contact is usually
where the thumbs meet in the middle — which is exactly where the merged-shape problem was
visible. Flush placement is unaffected: padding stays 0 on all four gloves.

### Tuner stage is plain black (2026-07-19)

The original sprite was drawn faintly behind the hands as an alignment ghost. On dark
gloves — invmgl especially — it showed through and muddled them, so the stage is now
plain black and the ghost is not drawn.

The `<img class="ghost">` element is KEPT at `opacity:0` rather than removed: the stage
geometry (and therefore the bounds frame, the cell grid and every clamp calculation) is
measured from its box, and `display:none` would collapse that to zero. The output bounds
frame and cell grid still mark where the sprite edges are, so alignment reference is not
lost — only the distracting artwork.

Tuner-only; the built DC6 has a transparent background and is unaffected.

### Checkerboard stage + threshold consistency (2026-07-19)

The tuner stage uses the app's standard `.checker` transparency pattern over a dark base,
matching the item thumbnails on the gallery and pairing pages. Plain black read oddly and
the original-sprite ghost muddled dark gloves, so this is the middle ground: the backdrop
announces "transparent" without competing with the artwork.

Two consistency fixes fell out of it:

- `silhouette_points()` traced at alpha > 8 while the flush snap measures at
  `VISIBLE_ALPHA` (16). The outline therefore included the faint rotation fringe and the
  clamp believed a provably-flush glove was 1-2% outside the border. Both now use
  `VISIBLE_ALPHA`.
- The clip badge no longer second-guesses an authored layout. Auto-fit positions are
  derived by MEASURING a real render server-side; the browser's polygon estimate (48
  sampled columns, geometric rotation) is coarser and still disagreed by ~1%, showing a
  false warning. While a layout is untouched the badge reads "auto-fitted flush to the
  border"; the live clip measurement returns as soon as you drag or move a slider.

### The cutout was punching holes through the artwork (2026-07-19)

Reported as "the gloves look slightly transparent". They were — genuinely, in the built
sprite as well as the preview. `drop_flat_background()` deleted EVERY pixel darker than
RGB 26 anywhere in the image, so on dark artwork it ate the glove's own shadows,
crevices, engraving and leather grain. invvgl measured only **10% fully opaque with 32%
partially transparent** and an interior alpha averaging 196/255; downsampling then smeared
the holes into a general haze.

Fixed by removing only background REACHABLE FROM THE IMAGE BORDER: label the dark
regions (`scipy.ndimage.label`), keep those touching an edge, and leave everything else
opaque. The backdrop is contiguous with the edges; a shadow inside a glove is not. A
pure-Python fallback (`_drop_flat_background_naive`) keeps the module working without
numpy/scipy.

Interior pixels rescued, i.e. holes that used to be punched through the art:

    invvgl-l5    83,526 px  (17.3% of the glove body)
    invmgl-l2    96,860 px  (23.7%)
    invtgl-lj2   98,976 px  (22.7%)
    invtgl-l4    59,641 px  (17.0%)
    invlgl-l2     2,468 px  ( 0.5%)   -- light leather, barely affected

Solid regions now measure 255/255 in both the source and the served thumbnail; the only
remaining partial alpha is edge antialiasing and the genuine gaps between fingers.

NOTE: any pair sprite built before this carries the holes baked in — rebuild those.

### "Still looks transparent" — three further causes (2026-07-19)

The cutout fix was correct (solid glove body measures 255/255 in the served PNG), but the
page still looked see-through for three separate reasons:

1. **No cache headers on the generated images.** `/api/pair/hand` returned a bare
   `Response(png)`, so browsers cached it heuristically and kept serving the pre-fix,
   hole-punched artwork indefinitely. Now every processed image carries
   `ETag: W/"<IMAGE_PIPELINE_VERSION>-<key>"` plus `Cache-Control: no-cache`, and the
   endpoint answers `If-None-Match` with a 304. Bumping `IMAGE_PIPELINE_VERSION` (now
   "2") invalidates every cached render, so a processing change can never again be
   masked by a stale browser cache.
2. **The mirrored slot preview was dimmed to opacity .75** — my own CSS. It is a real
   preview of what gets built, so it now renders at full strength.
3. **Slot thumbnails had no backing.** Cut-out art sat directly on the brown panel while
   the generation cards next to them show raw Meshy renders on their own black
   background, so the cut-outs read washed out by comparison. They now get the same dark
   base as the tuner stage under the checker.

Reminder: a pair sprite BUILT before the cutout fix has the holes baked into its DC6.
Rebuild those from the pairing page.

### Hybrid 3D pair renderer (2026-07-20)

Gloves with a Meshy model now render as a real 3D pair instead of two composited 2D
images: the model is loaded once, duplicated, and mirrored with `scale.x = -1`, then one
hand is yawed inward and pushed toward the camera so it genuinely occludes the other.
Mirroring the MODEL rather than the render is the key — both hands are lit by the same
scene lights, so highlights fall correctly on each, which a 2D flip cannot do because it
flips the lighting with the image.

**Two engines, chosen at build time.** Blender (`--pair`, Cycles) and the browser
(`app/static/pair3d.js`, three.js/WebGL). Blender is optional: `/api/pair/engines`
reports availability and the prompt shows it disabled with a reason when absent, so the
browser path always works.

**The rigs are deliberately matched** — same orthographic camera on the same azim/elev
sphere, the same two suns converted from Blender's `rotation_euler` into three's Y-up
axes, the same ambient. Measured on the same model and pose:

    silhouette IoU            0.995   (bboxes within 1px, opaque counts 0.7% apart)
    mean |luma diff| inside   39.3 -> 25.0 -> 17.7 / 255 after calibration

Cycles gathers indirect bounce light that WebGL's direct-only model does not, so at
identical light values the browser rendered darker (mean luma 142 vs 181). `CYCLES_GAIN`
(1.45, iterated against real renders) closes most of it. The residual ~18/255 is soft
contact shading Cycles resolves and shadow maps approximate.

Gotchas found while building this:
- `make_pair()` must call `view_layer.update()`; `matrix_world` is stale until the
  dependency graph re-evaluates, so framing used PRE-pose transforms and cropped the pair.
- Mirrored meshes need their winding handled. In Blender that is `flip_normals()`. In
  three, `BackSide` is WRONG for these meshes because they are not closed — the open cuff
  rendered as unlit black. `DoubleSide` with `shadowSide = FrontSide` is correct.
- `toneMappingExposure` is ignored unless tone mapping is enabled; the gain has to be
  applied to the light intensities.
- A 3D pair render is a COMPLETE sprite, so it goes straight through
  `assets.png_to_item_dc6` (crop-to-content + fill) — flush and centred for free, none of
  the 2D placement machinery.

**End-to-end verified (2026-07-20).** The same glove built through both engines:

    Blender : POST /api/pair/build3d/blender  -> 58x58 DC6, padding 0/0/1/1, 4.4s
    browser : capture 464x464 -> /api/pair/build3d -> 58x58 DC6, padding 0/0/1/1, instant

At sprite size the two are near-indistinguishable, which is the result the hybrid is
built on: iterate in the browser, and render the final in whichever engine you pick.

### Live 3D pose tuner (2026-07-20)

A glove row whose selected generation has a model opens a LIVE 3D tuner instead of the
flat-image one: the real mirrored pair rendered in three.js, with sliders for turn-inward
(yaw), separation (gap), one-hand-forward (depth) and the camera's azimuth/elevation.
Dragging re-renders immediately — the whole point of the hybrid.

The pose is stored per art file as `pose3d` inside `pair_templates.json`, so it survives
restarts and BOTH engines build from it: the Blender route reads the saved pose and the
browser build passes it into the same `PairPreview`. Verified end to end — a pose saved
as yaw 34 / gap 0.85 came back in the built sprite's provenance as exactly that, and the
sprite is visibly different from a default-pose build.

`pose3d` is part of `DEFAULT_TEMPLATE`, not just merged in later: `get_template()` returns
early when nothing is saved, so a pose added only on the merge path came back null for
every untuned glove.

Art with no model still opens the flat-image tuner and builds through the 2D compositor,
so nothing regressed.

### 3D pair thumbnails on the generation cards (2026-07-20)

A glove generation's card now shows the PAIR it will actually become rather than a single
model, with the reference art you fed Meshy moved to the small inset so you can still see
what each candidate came from when choosing between several gauntlets.

Rendered ONCE in the browser at the art file's saved `pose3d` (or the default), uploaded
to `meshy_cache/pair_thumbs/<task>.png`, and served from there afterwards — the models are
~7MB each and a row can hold four, so re-rendering per visit would make the page unusable.
Saving a new pose clears that art file's thumbnails so they re-render.

Two things worth recording:
- The queue is driven by the server's `has_thumb` flag, NOT by the `<img>` onerror
  handler. `queueThumbs()` runs immediately after the markup is inserted, before the
  browser has attempted the requests, so nothing was ever marked missing and no
  thumbnails rendered at all.
- Pair rendering is restricted to SPLIT-HAND art (`p.pairable && g.hand`). Applied to
  everything it produced two brains for the Cerebral Nut, two scrolls for the Town Portal
  Scroll, and four boots for the greaves — whose model is already a pair. Everything
  non-pairable keeps its single-model preview.

Measured: 13 glove thumbnails rendered and cached in under 25s on first view; later
visits load cached PNGs with no GLB download.
