# Ghidra investigation — item-art cache + item spawn (PD2 binaries)

Resumed 2026-07-18. PD2 binaries open in Ghidra (`/Mods/PD2-S12/*`, matching
`C:\Diablo2\ProjectD2_backup`). Image bases: D2Client `6fab0000`, D2Game `6fc20000`,
D2Common `6fd50000`, D2Win `6f8e0000`, D2CMP `6fe10000`.

Purpose: pin callable addresses/signatures for (Q3) an in-process `/showcase/item` spawn verb,
and (Q1/Q2) the client item-art cache eviction target for future Tier-B live reload.

Findings accumulate below as they're verified. Addresses are absolute (rebased PD2 loads).

## Q3 — D2Game item-spawn entry points

Candidate creators (from function-name search in D2Game.dll):
- `ITEMS_CreateItemStack @ 6fc307e0`
- `ITEMS_CreateItemUnitFromParsed @ 6fc2eae0`
- `ITEMS_CreateItemUnit @ 6fc31490`
- `CreateItemWithParams @ 6fc31880`
- `ITEMS_CreateItemWithLevel @ 6fc31980`
- `CreateItemByCodeAlt @ 6fc8afa0`
- `ITEMS_CreateItemAndEquip @ 6fcc1420`
- `ITEMS_CreateItemFromSkillContext @ 6fc8aee0`

### PRIMARY spawn primitive (verified by decompile)
**`ITEMS_CreateAndDropItem(Game *pGame, UnitAny *pUnit, int nItemCode, BOOL bDrop)` @ `6fc8b070`**
— creates an item of `nItemCode` in `pUnit`'s room and, if `bDrop != 0`, drops it at the
unit's position (via `ITEMS_DropItemAtUnitPosition`). This is the exact "spawn at the player's
feet" verb: pass the server Game*, the player UnitAny*, the item's tbl index, bDrop=TRUE. The
dropped item shows the flippy on landing; picking it up shows the inventory art. Returns a
status code (0 fail / 1 created-no-drop / 0x46 dropped), NOT a unit pointer.

Supporting chain (all in D2Game, verified):
- `ITEMS_CreateItemUnit(Game*, ITEMS_ItemCreationDesc*) @ 6fc31490` — the core allocator; reads
  `pItemDesc->pField14` (classId = ItemsBIN tbl index), quality, level, RNG seeds, etc.
- `CreateItemWithParams(Game*, uint dwItemCode, int nQuality, ITEMS_ItemCreationDesc* pQualityData, uint dwDropFlags) @ 6fc31880`
  — mid-level: zero-fills a 0x98-byte desc, sets sourceMode=1 (player-created), createMode=3,
  quality from param, then calls ITEMS_CreateItemUnit. Returns item GUID. Good for forcing
  quality (unique/set) via nQuality + pQualityData.
- `CreateItemByCodeAlt @ 6fc8afa0` — resolves a 4-char code ('uap ') via `GetItemDataByCode`
  → index, then ITEMS_CreateItemUnit (thiscall-ish: item code in EAX, source unit in EBX —
  register args, awkward to call directly).
- `ITEMS_DropItemAtUnitPosition(UnitAny*) @ 6fcf2d90` — the world-placement step (relies on
  register/inline item-carrier context; call via ITEMS_CreateAndDropItem, not standalone).
- Quality forcing (unique index / set): `CreateItemWithParams` nQuality param + pQualityData;
  quality codes still to confirm (unique vs set enum). For a SPECIFIC unique (Shako = Harlequin
  Crest) the unique row index goes in the quality data — exact field TBD (see open items).

### nItemCode semantics
`nItemCode` is the ItemsBIN **tbl index** (classId), not the 4-char code. Resolve base item
index from the item's code via `GetItemDataByCode` (D2Common) or precompute from the extracted
tables. Shako base = 'uap'.

### ★ COMPLETE /showcase/item RECIPE (verified against ITEMS_FindItemByDataCode @ 6fc8b430)
The game's own quest-drop code uses exactly this pattern (pContext[0]=Game*, pContext[2]=player):
```
idx = DATATBLS_GetItemDataByCode('uap ')            // 4-char code (DWORD) -> tbl index, -1 = fail
ITEMS_CreateAndDropItem(pGame, pPlayerUnit, idx, TRUE)   // drops at player's feet (flippy shows)
```
Pinned addresses / signatures (PD2 rebased):
- `ITEMS_CreateAndDropItem @ 6fc8b070` — `int __stdcall(Game* pGame, UnitAny* pUnit, int nItemCode, BOOL bDrop)`.
  bDrop=TRUE → drop at feet (flippy + pick-up shows inv art); bDrop=FALSE → create only.
- `DATATBLS_GetItemDataByCode @ 6fd9e1d0` (D2Common) — `int __stdcall(code, 0, 0)`; arg0 is the
  4-char code DWORD (e.g. 'uap ' = 0x20706175). D2Game `GetItemDataByCode @ 6fc2ae74` is a 5-byte
  thunk to it. (Alternatively precompute the index from the extracted armor/misc/weapons tables —
  row order = classId — and skip this call entirely.)
- To force a SPECIFIC quality (unique/set) instead of the base item, use
  `CreateItemWithParams @ 6fc31880` (`int __stdcall(Game*, uint code, int quality, ItemCreationDesc* qdata, uint dropFlags)`)
  then place via the drop path — quality enum + the unique-index field in the desc still TBD.

Corrected addresses (verified 2026-07-18 by disassembly):
- The real code→classId resolver is **`ITEMS_GetDataByCode @ 6fdc1940`** (D2Common), `int __stdcall(uint dwCode)`,
  `RET 0x4` (ONE arg). It does `BinarySearchInSortedArray(g_pItemDataBuffer@0x6fdeff6c, dwCode, 0)`
  and returns the classId, -1 if not found. The D2Game thunk `GetItemDataByCode @ 6fc2ae74` is a
  bare `JMP [0x6fd1898c]` to it. **NOTE:** the function Ghidra had labeled `DATATBLS_GetItemDataByCode
  @ 6fd9e1d0` is a MISLABEL — it is a pure `return **(short**)arg` double-deref, not a code lookup;
  a correcting plate comment was written to Ghidra (D2Common saved).

### Spawn-verb STATUS (2026-07-18, latest): server-side item CREATION WORKS; only the DROP faults
**`POST /showcase/item {code, drop:false}` SUCCEEDS** — a Shako ('uap') is created server-side,
no fault, diag stage 8. Verified the full chain end-to-end:
- Server `Game*` captured by hooking `GAME_ProcessGameFrameTick @ 6fc4e050` (game in EAX) and the
  spawn now EXECUTES inside that frame-tick handler (SERVER context, under SEH) — not the client
  capture pump. This was necessary: creation touches server state.
- Player walk `Game.pClientList(+0x88) -> GameClient.pPlayer(+0x174)` yields the REAL player,
  confirmed by live diagnostics: `dwType=0, dwClassId=2 (Necromancer, matches the launched
  "summoner-skele" char), dwUnitId=1, pPath=0x02460a00 (valid)`. Offsets 0x88/0x174 are CORRECT
  for PD2.
- `ITEMS_GetDataByCode('uap ') = 435` (correct classId). `ITEMS_CreateAndDropItem` ABI is
  `int __stdcall(Game*, UnitAny*, int, BOOL)`, RET 0x10 (verified by disasm).

**Only `drop:true` faults** (SEH-caught, game unharmed) — isolated to the DROP path
(`ITEMS_DropItemAtUnitPosition @ 6fcf2d90` + the command-write sequence
`ValidateAndWriteCommand @ 6fcac810` that ITEMS_CreateAndDropItem runs when bDrop!=0). Likely the
client-notify/command context isn't valid at the frame-tick-entry point. **Two ways to finish
(next increment):** (a) fix the drop context (run at a different point in the tick, or set up the
command buffer), OR (b) BETTER for inv-art testing — create the item (works) and place it in the
player's INVENTORY via a separate function: use `CreateItemWithParams @ 6fc31880` (returns item
GUID) or `ITEMS_CreateItemUnit @ 6fc31490` (returns UnitAny*), then an inventory-add call — this
shows the inventory DC6 directly (the priority art surface) and avoids the flippy/drop path
entirely. Diagnostics via `/asset/status` `spawnDbg` (stage/game/client/player/classId/playerCls/
playerId/playerPath) are in place. Route + frame-tick handler in `D2Debugger.assetreload.cpp`.

### RESOLVED (2026-07-18): injected-call item spawn is ARCHITECTURALLY BLOCKED — use save injection
Traced every placement path; all blocked by the SP client/server split + command context:
- Drop tail `ValidateAndWriteCommand @ 6fcac810` -> `NET_WriteServerCommandPacket` validates an
  unwind-index vs a live GameContext and writes a server->client packet; injected calls lack that
  context -> fault (timing-independent; tested frame-tick entry AND exit).
- `ITEMS_PickupGroundItem @ 6fcf76a0` needs the item already in the player-game-data ground hash
  (+0x1720, GUID key, pNext==0x4 on-ground, quality==NORMAL) -> needs a working drop first.
- `INV_PlaceItemIntoInventoryPage @ 6fd718a0` updates only SERVER inventory; SP client renders its
  own copy -> needs the (faulting) sync packet.
- `ITEMS_CreateItemAndEquip @ 6fcc1420` needs an equip-context struct.
Create itself works: `CreateItemWithParams @ 6fc31880` (EBX=source unit) returns the item UnitAny*.
**Reliable path: SAVE-FILE (.d2s) INJECTION** at the menu (no runtime sync needed). The candidate
in-game functions below are retained for reference but are NOT usable from injected calls.

### (reference, NOT usable from injected calls) Make-item-visible candidate paths
**Path B — TURNKEY RECIPE (disassembly-verified 2026-07-18).** Two register-convention calls,
both needing a small inline-asm wrapper (creation already runs correctly in the server frame-tick
handler; add these there):

1) Create the item and GET ITS POINTER. Best option:
   `CreateItemWithParams @ 6fc31880` — verified ABI: **EBX = source unit (player)** (it does
   `GetUnitLevelPosition(EBX)`), plus __stdcall stack args `(Game*, classId, quality, qdata, dropFlags)`,
   `RET 0x14`, returns the item `UnitAny*` in EAX. Wrapper:
   ```
   void* CreateItem(void* game, int classId, int quality, void* player) {
     void* fn = (void*)(d2game + 0x11880); void* r;
     __asm { mov ebx, player
             push 0; push 0; push quality; push classId; push game
             call fn ; stdcall cleans 0x14
             mov r, eax }
     return r; // item UnitAny*, or 0
   }
   ```
   `quality` value still to confirm (check a CreateItemWithParams caller; try a normal-quality
   constant). ALT creator: `CreateItemByCodeAlt @ 6fc8afa0` — EAX=4-char code, EBX=player,
   __stdcall`(Game*, arg2)` RET 0x8 (arg2@[EBP+0xc] TBD), returns item; uses default quality like
   the working ITEMS_CreateAndDropItem create, so may be simpler (no quality param) once arg2 is known.

2) Place it: `PLAYER_PlaceItemInInventory(UnitAny* item, UnitAny* player, Game* game, int a8=1)` —
   D2Game `__fastcall` (item=ECX, player=EDX, then game, a8 on stack). D2MOO body: sets inv page
   (INVPAGE_INVENTORY) then calls `D2GAME_PlaceItem(...item->dwUnitId, GetUnitX(item), GetUnitY(item), a8, 1, 0)`.
   **PD2 address NOT in the Ghidra DB yet** — find it (search callers of the item-pickup path, or
   match the Player.cpp:269 body). fastcall wrapper: `mov ecx, item; mov edx, player; push a8; push game; call fn`.

Run both in the frame-tick handler (server context, where create works). Result: the item lands in
the player's inventory and its inventory DC6 renders -- the priority art surface. Guard with SEH
(already present) + LooksLikePtr on the created item before placing.

**(reference) Path B — original notes:**
- `CreateItemWithParams @ 6fc31880` — `int __stdcall(Game*, uint code, int quality, ItemCreationDesc* qdata, uint dropFlags)`.
  Returns the created item `UnitAny*` (it returns `ITEMS_CreateItemUnit`'s result, which is the
  unit pointer — Ghidra's "GUID" label is wrong). **CAVEAT:** its body derefs **EBX** (a phantom
  register param) in a `nX==1 && nY==0x187` magic check -> call it from a tiny naked wrapper that
  zeroes EBX first, or EBX-garbage will fault. `code`=classId (435 for 'uap'), quality/qdata can
  be 0 for a plain base item.
- `PLAYER_PlaceItemInInventory(UnitAny* pItem, UnitAny* pPlayer, Game* pGame, int a8=1)` — the
  server-side inventory placer (D2MOO name; D2Game `__fastcall`). **NOT named in the PD2 Ghidra DB
  yet** -- find its PD2 address (search callers of an inventory-grid function, or by the
  Player.cpp:269 body signature). fastcall: pItem=ECX, pPlayer=EDX, (pGame, a8) on stack.
- Sequence (in the server frame-tick handler, where creation already works):
  `item = CreateItemWithParams(game, 435, 0, 0, 0); if (item) PLAYER_PlaceItemInInventory(item, player, game, 1);`
  Shows the inventory DC6 directly -- the priority art surface.

**Path A — fix drop-at-feet: RULED OUT (timing-independent).** The clean
`ITEMS_CreateAndDropItem(game, player, 435, TRUE)` create works; only its drop tail faults
(`ValidateAndWriteCommand @ 6fcac810` + `ITEMS_DropItemAtUnitPosition @ 6fcf2d90`). TESTED running
the spawn at frame-tick EXIT (stub calls the trampoline to run the tick body first, then does the
pending spawn) -- STILL faults at stage 7. So the fault is NOT a frame-tick entry-vs-exit timing
issue; it's intrinsic to the drop's per-command/client-notify context (the game's own loot drops
run inside a specific command-processing context we don't reproduce). Conclusion: **use Path B
(inventory) instead of trying to fix the drop.** The current deployed stub runs the spawn post-tick
and creation works reliably with it.

### (superseded) STATUS: server handle SOLVED; faults inside the call
Resolved the server-handle sourcing and instrumented the path. Current state:
- **Server `Game*` capture: WORKING.** Hooked `GAME_ProcessGameFrameTick @ 6fc4e050` (D2Game RVA
  0x2E050) — the per-frame server tick, which receives the game in **EAX** (register-passing;
  `void __stdcall(void)`, `RET`, game in EAX). A naked stub grabs EAX every frame. Installed at
  D2Debugger startup + lazily per spawn. (NOTE: the obvious `GAME_UpdateProgress` piggyback does
  NOT work on PD2 — D2Debugger's patch hardcodes D2Game offset 0x54400, which is
  `PLAYER_HandleDisconnect` in PD2, not GAME_UpdateProgress, so that hook is misaligned/dead here.
  And `GAME_UpdateGameTick @ 6fd02600` is a unit-event handler, NOT per-frame — wrong target.)
- **Server player walk: RESOLVES.** `Game.pClientList (+0x88) -> GameClient.pPlayer (+0x174)`
  yields a plausible player (dwType 0 at +0). Live diagnostics (via `/asset/status` `spawnDbg`)
  from an actual attempt: `serverGame=0x0e0f007c, client=0x0e310000, player=0x0e10e100, classId=435`.
- **classId: CORRECT.** `ITEMS_GetDataByCode('uap ')` returned 435 (valid Shako-base index).
- **ABI: VERIFIED by disasm.** `ITEMS_CreateAndDropItem @ 6fc8b070` is `RET 0x10` (4 __stdcall
  args), pGame=[EBP+8], pUnit=[EBP+0xC]. My call matches.
- **FAULT: inside `ITEMS_CreateAndDropItem` (diag stage 7), SEH-caught, game survives.** The very
  first thing it does is `GetUnitLevelPosition(pUnit)`. With all args resolving to valid-looking
  pointers, the leading hypothesis is **execution context**: the spawn runs from the CLIENT-side
  D2Common capture pump (`D2Gt_Pump`), but server item-creation almost certainly requires the
  **server game lock / server-frame context** held (the game's own quest-drop code calls
  ITEMS_CreateAndDropItem from inside the server tick). Secondary hypothesis: a subtle
  player-pointer validity issue (pPath not yet valid) — less likely given dwType==0 checks out.
- **FIX DIRECTION (next increment):** execute the spawn from INSIDE the `GAME_ProcessGameFrameTick`
  hook (server context, lock held) instead of the client capture pump — i.e. a one-shot request
  flag the frame-tick stub consumes. The diagnostics endpoint is in place to confirm.

### (superseded) Earlier spawn-verb status (client-side capture pump)
`ITEMS_CreateAndDropItem` and `ITEMS_GetDataByCode` are both clean __stdcall — wired into the
AssetReload subsystem's **`POST /showcase/item {code, drop, confirm}`** route (built + deployed).
The route resolves both by GetModuleHandle+RVA and calls them on the game thread. ABI verified
correct. **Open blocker:** it needs the SERVER `Game*` + SERVER player `UnitAny*`, and the
D2Debugger capture hook (UNIT_UpdateUnitCollisionOrLight, D2Common+0x35800) captures the
**CLIENT** player in single-player. Proof: `UnitAny.pGame` at +0x80 is a *union* member — for a
server unit it's `Game*` (D2Game AI reads `pUnit->pGame`, AiBaal.cpp:323), but for a client unit
the same offset is `dwSfxAsyncTicks` (a tick count). The route's pointer-sanity guard correctly
detects the client unit (pGame fails the heap-range check) and returns a clear error instead of
crashing. **Remaining path to finish (next increment):** get the server `Game*` via
`GAME_GetGameByClientId(clientId)` (D2Game Game.cpp:2365 — involves SERVER_GetClientGameGUID +
GAME_LockGame + the SP client id) or a server-function capture hook, then map the captured client
player's GUID (UnitAny+0x0C) to the server unit via `SUNIT_GetServerUnit(pGame, UNIT_PLAYER, guid)`
(used at Clients.cpp:195). Then `ITEMS_CreateAndDropItem(serverGame, serverPlayer, classId, TRUE)`.
The struct/ABI facts are all confirmed; only the server client-list globals + SUNIT_GetServerUnit
address remain to pin. Route lives in `D2Debugger.assetreload.cpp` (SpawnItemImpl).

## Q1/Q2 — client item-art cache (Tier-B eviction target)

- `CLIENT_DrawCursorItemSprite @ 6fac6910` draws the cursor-held item via
  `CLIENT_ProcessUnitGraphicsEffect` (returns a graphics handle) — client-side graphics prep,
  not the DC6 loader itself.
- `CLIENT_DrawInventoryGridCells @ 6fb3c600` = empty-cell backgrounds only (not item sprites).
- Item sprites render through D2Client's **Gfx vtable interface** (`GfxInterfaceCall84 @ 6fabd096`
  and the `GfxInterface_CallMethod0x*` thunks at 6fabd0xx → the D2gfx backend). The per-item
  sprite is resolved from the item unit's **client-side GfxInfo** (per-unit graphics struct;
  field accessors e.g. `CLIENT_GetUnitGfxInfoField68 @ 6fb54b30`, `GetGfxCellSize @ 6fb58700`).
  The inv `CellFile*` is loaded on demand (via D2Win `ARCHIVE_LoadCellFile` #10039) and cached
  in that per-unit GfxInfo; decompressed cells live in the **D2CMP sprite cache**.

### Q1/Q2 verdict (confirms earlier cache-map exploration)
- **Targeted Tier-B eviction** = null the item unit's GfxInfo inv-`CellFile*` (forces a reload)
  **+** flush the D2CMP sprite cache. D2CMP exposes only a **whole-cache flush** (`#10053`),
  no per-entry eviction — so the coarse lever is: clear the unit's cached cell pointer, call
  `#10053`, next draw re-loads. The exact GfxInfo field offset holding the inv `CellFile*` is
  the one implementation detail left to nail during Phase-1 Tier-B work (it's per-unit; trace
  the store site of `ARCHIVE_LoadCellFile`'s return for an item unit).
- **No separate d2gl item-texture disk cache** (established in Phase 0 §12), so an evict+reload
  will surface a changed DC6 **once the override channel (patch.mpq registration) works**.
  Tier B stays a later optimization; **Tier C (soft reload) is the day-1 floor** and needs none
  of the above.
