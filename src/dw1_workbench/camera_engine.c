/*
 * Zoomed-out background renderer for Digimon World (USA, SLUS_010.32), run inside the game.
 *
 * The game keeps a 4x3 ring of full-size 128x128 background tiles in VRAM (x 768-1023, y 0-383) and draws
 * them 1:1 (dw_decomp src/main/map.c: renderMap, setupMap, uploadMapTileImages, updateTileRow/Column).
 * A zoomed-out view needs more tiles than fit at full size, but each tile is then drawn only T = 128*scale
 * pixels wide, so this engine keeps its own ring of tiles downscaled to T x T in the same VRAM area, built
 * from the full-size tile data the game already holds in RAM, and draws the visible ones with the same
 * transform the foreground hook applies, around the screen centre. Tiles are drawn behind the game's drawing-
 * offset switch at ordering-table entry 32, so the GPU adds DRAWING_OFFSET_X/Y to them, while characters (in
 * front) get the screen centre (160, 120) with GTE offset 0: tiles therefore scale around primitive
 * (160 - DRAWING_OFFSET_X, 120 - DRAWING_OFFSET_Y), where characters shrink to.
 *
 * camera.py installs it with renderMap's tile loop redirected here and the game's own tile uploads turned
 * off, and describes the ring layout in the Params block. Build: tools/build_camera_engine.py.
 * No division (the R3000 build must not trap), no globals, no library calls but the game's own.
 */
typedef unsigned char u8;
typedef signed char s8;
typedef short s16;
typedef unsigned short u16;
typedef int s32;
typedef unsigned int u32;

#define AT(address, type) (*(volatile type *)(address))

/* USA addresses, from jype0/dw_decomp config/us/symbols.txt */
#define MAP_TILE_DATA 0x8014F084u /* MapTileData tiles[35]; ... cameraX +0x1BB4 */
#define CAMERA_X (MAP_TILE_DATA + 0x1BB4)
#define CAMERA_Y (MAP_TILE_DATA + 0x1BB6)
#define MAP_WIDTH (MAP_TILE_DATA + 0x1BB8)
#define MAP_HEIGHT (MAP_TILE_DATA + 0x1BB9)
#define DRAWING_OFFSET_X 0x80134EC0u
#define DRAWING_OFFSET_Y 0x80134EC4u
#define ACTIVE_ORDERING_TABLE 0x80134ED0u /* GsOT *, org at +4 */
#define CURRENT_SCREEN 0x80134DA8u

typedef struct {
    u8 *imagePtr;
    s16 tileId;
    s16 posX, posY;
    s16 texU, texV;
    s16 tpage, clut;
} MapTileData; /* 20 bytes */

typedef struct {
    u32 tag;
    u8 r0, g0, b0, code;
    s16 x0, y0; u8 u0, v0; u16 clut;
    s16 x1, y1; u8 u1, v1; u16 tpage;
    s16 x2, y2; u8 u2, v2; u16 pad1;
    s16 x3, y3; u8 u3, v3; u16 pad2;
} PolyFT4;

typedef struct { s16 x, y, w, h; } Rect;

typedef void *(*GetWorkBase)(void);
typedef void (*SetWorkBase)(void *);
typedef void (*SetPoly)(void *);
typedef void (*AddPrimFn)(void *, void *);
typedef int (*LoadImageFn)(Rect *, void *);
typedef int (*DrawSyncFn)(int);
#define GsGetWorkBase ((GetWorkBase)0x80098C38u)
#define GsSetWorkBase ((SetWorkBase)0x80098C28u)
#define SetPolyFT4 ((SetPoly)0x80092B88u)
#define AddPrim ((AddPrimFn)0x80092AD4u)
#define LoadImage ((LoadImageFn)0x800948A8u)
#define DrawSync ((DrawSyncFn)0x800947B0u)
typedef void (*SetProjectionFn)(s32);
#define GsSetProjection ((SetProjectionFn)0x800981C8u)
/* 16.16, unlike the library's SetGeomOffset: half pixels stop characters stepping a pixel as the camera moves. */
static void set_geom_fine(s32 x, s32 y)
{
    register s32 ofx __asm__("$8") = x;
    register s32 ofy __asm__("$9") = y;
    /* ctc2 $8, $24; ctc2 $9, $25: the assembler won't take coprocessor 2 for mips1. */
    __asm__ volatile(".word 0x48C8C000\n\t.word 0x48C9C800\n\tnop\n\tnop" : : "r"(ofx), "r"(ofy));
}
#define GPU_OFFSET_X 0x80134EDCu /* DRAWING_OFFSET_*_COPY: what the GPU applies this frame, set before the camera moves */
#define GPU_OFFSET_Y 0x80134EE0u
#define ENTITY_TABLE 0x8012F344u
#define ENTITY_IS_ON_MAP 52
#define ENTITY_IS_ON_SCREEN 53
typedef s32 (*EntityIsOffScreenFn)(void *, s32, s32);
#define entityIsOffScreen ((EntityIsOffScreenFn)0x800D5430u)
typedef void (*RenderObjectsFn)(void);
#define renderObjects ((RenderObjectsFn)0x800A315Cu)
typedef void (*SortSpriteFn)(void *, void *, s32);
#define VIEWPORT_DISTANCE 0x80134ED4u /* camera.py keeps it at the map's value times the zoom; fixed framing, while drawing */

#define MAX_SLOTS 48
typedef struct {
    s32 magic;            /* 'DWZM' while installed */
    s32 frames;           /* incremented every call: proves the engine runs */
    s32 uploads;          /* tiles uploaded since install */
    s16 scale;            /* 256 = 100% */
    s16 inverse;          /* 65536 / scale, 8.8 fixed point */
    s16 tile;             /* T: texels per downscaled tile side (even) */
    s16 columns, rows;    /* ring size */
    s16 budget;           /* uploads allowed per frame */
    s16 slot_x[8];        /* VRAM x (16-bit units) of each ring column */
    s16 slot_y[8];        /* VRAM y of each ring row */
    s16 map_screen;       /* map the ring was filled for (-1: refill) */
    s16 map_width, map_height;
    s16 pad;              /* 1: fixed-framing diagnostic, projection is render-only; 0: legacy prototype */
    u32 map_first_image;  /* tiles[0].imagePtr of that map */
    s8 resident[MAX_SLOTS]; /* tile index held by each slot, -1 none */
    u8 sample[128];       /* source texel for each downscaled texel: (i * 128 + 64) / T */
    s32 drawn;            /* tiles drawn in the last frame */
    u32 first_prim;       /* address of the last frame's first tile primitive (diagnostics) */
} Params;

/* camera.py writes this block; keep both layouts in step. */
#define OFFSET(member) __builtin_offsetof(Params, member)
_Static_assert(OFFSET(scale) == 12 && OFFSET(tile) == 16 && OFFSET(budget) == 22, "Params layout");
_Static_assert(OFFSET(slot_x) == 24 && OFFSET(slot_y) == 40 && OFFSET(map_screen) == 56, "Params layout");
_Static_assert(OFFSET(map_first_image) == 64 && OFFSET(resident) == 68 && OFFSET(sample) == 116, "Params layout");
_Static_assert(OFFSET(drawn) == 244 && sizeof(Params) == 252 && sizeof(MapTileData) == 20 && sizeof(PolyFT4) == 40, "layout");

#ifndef DW1_ENGINE_PARAMS
#define DW1_ENGINE_PARAMS 0x801D3000u
#endif
#ifndef DW1_ENGINE_BUFFER
#define DW1_ENGINE_BUFFER 0x801C0000u
#endif
#define PARAMS ((Params *)DW1_ENGINE_PARAMS)
#define BUFFER ((u8 *)DW1_ENGINE_BUFFER) /* T * T bytes (up to 128 * 128) */

/* Fixed framing, after Params. sprite_entry is GsSortSprite's displaced first words and a jump back (fixed_framing.py). */
typedef struct {
    s32 geom_x, geom_y;
    u32 sprite_entry[4];
    s32 drawing, native_distance;
    u32 native_entries[6][4]; /* the same for fixed_framing.py's NATIVE_CAMERA functions, in that order */
} FixedState;
#define FIXED ((FixedState *)(DW1_ENGINE_PARAMS + 0x100u))
_Static_assert(sizeof(Params) <= 0x100 && sizeof(FixedState) <= 0xE0, "Params or FixedState overflow the block");

static s32 mod_small(s32 value, s32 divisor)
{
    while (value >= divisor)
        value -= divisor;
    return value;
}

static s32 scaled(s32 coordinate, s32 centre, s32 scale)
{
    return centre + (((coordinate - centre) * scale) >> 8);
}

/* Fixed proof: keep the expanded viewport inside existing artwork when the map is large enough.
   Translation is expressed as an affine anchor, shared by 2D corners and GTE geometry. */
static s32 fixed_anchor(s32 camera, s32 extent, s32 screen, s32 scale)
{
    s32 rest = 256 - scale;
    s32 shift = (screen >> 1) * rest;
    s32 low = screen * 256 - (extent - camera) * scale;
    s32 high = camera * scale;
    if (extent * scale < screen * 256)
        return screen >> 1; /* finite art cannot cover the expanded view; controller must reject it */
    if (shift < low) shift = low;
    if (shift > high) shift = high;
    return shift / rest;
}

static void upload(Params *p, MapTileData *tile, s32 column, s32 row)
{
    s32 t = p->tile, u, v;
    u8 *out = BUFFER;
    Rect rect;
    for (v = 0; v < t; v++) {
        const u8 *line = tile->imagePtr + (p->sample[v] << 7);
        for (u = 0; u < t; u++)
            *out++ = line[p->sample[u]];
    }
    rect.x = p->slot_x[column];
    rect.y = p->slot_y[row];
    rect.w = (s16)(t >> 1);
    rect.h = (s16)t;
    DrawSync(0); /* the previous upload may still be reading BUFFER */
    LoadImage(&rect, BUFFER);
    DrawSync(0);
    p->uploads++;
}

void zoom_render_tiles(void)
{
    Params *p = PARAMS;
    MapTileData *tiles = (MapTileData *)MAP_TILE_DATA;
    s32 width = AT(MAP_WIDTH, s8), height = AT(MAP_HEIGHT, s8);
    s32 camera_x = AT(CAMERA_X, s16), camera_y = AT(CAMERA_Y, s16);
    s32 ox = AT(DRAWING_OFFSET_X, s32), oy = AT(DRAWING_OFFSET_Y, s32);
    s32 scale = p->scale, inverse = p->inverse, t = p->tile;
    s32 centre_x = 160, centre_y = 120;
    s32 x0, x1, y0, y1, tx, ty, budget = p->budget, i;
    u32 *ot;

    p->frames++;
    GsSetProjection(AT(VIEWPORT_DISTANCE, s32)); /* characters follow the zoom even on maps without foreground */
    if (width <= 0 || height <= 0 || width * height > 35)
        return;
    if (p->pad == 1) {
        /* tickCameraFollowPlayer has just moved the camera, and the drawing offset by the opposite amount, but the GPU
           still uses the old offset. The two cancel at the game's own scale and wouldn't at ours: the background would
           jump by each step's size. So draw from where the camera was when the frame began, like the characters. */
        camera_x += ox - AT(GPU_OFFSET_X, s32);
        camera_y += oy - AT(GPU_OFFSET_Y, s32);
        ox = AT(GPU_OFFSET_X, s32);
        oy = AT(GPU_OFFSET_Y, s32);
        centre_x = fixed_anchor(camera_x, width * 128, 320, scale);
        centre_y = fixed_anchor(camera_y, height * 128, 240, scale);
    }
    if (p->map_screen != AT(CURRENT_SCREEN, u8) || p->map_width != width || p->map_height != height
        || p->map_first_image != (u32)tiles[0].imagePtr) {
        p->map_screen = AT(CURRENT_SCREEN, u8);
        p->map_width = (s16)width;
        p->map_height = (s16)height;
        p->map_first_image = (u32)tiles[0].imagePtr;
        for (i = 0; i < MAX_SLOTS; i++)
            p->resident[i] = -1;
        budget = MAX_SLOTS; /* a new map: fill the whole view now rather than over several frames */
    }

    /* Screen x = prim x + ox and native prim x = mapX - camera_x - ox, so after scaling around the screen
       centre the screen shows map x camera_x + 160 +- 160 / scale (likewise y with 120). */
    x0 = camera_x + centre_x - ((centre_x * inverse) >> 8);
    x1 = camera_x + centre_x + (((320 - centre_x) * inverse) >> 8);
    y0 = camera_y + centre_y - ((centre_y * inverse) >> 8);
    y1 = camera_y + centre_y + (((240 - centre_y) * inverse) >> 8);
    x0 = x0 < 0 ? 0 : x0 >> 7;
    y0 = y0 < 0 ? 0 : y0 >> 7;
    x1 = x1 < 0 ? -1 : x1 >> 7;
    y1 = y1 < 0 ? -1 : y1 >> 7;
    if (x1 >= width) x1 = width - 1;
    if (y1 >= height) y1 = height - 1;
    if (x1 - x0 + 1 > p->columns) x1 = x0 + p->columns - 1;
    if (y1 - y0 + 1 > p->rows) y1 = y0 + p->rows - 1;

    ot = (u32 *)(*(u32 *)(AT(ACTIVE_ORDERING_TABLE, u32) + 4)) + 0xFFF;
    p->drawn = 0;
    p->first_prim = 0;
    for (ty = y0; ty <= y1; ty++) {
        s32 row = mod_small(ty, p->rows);
        for (tx = x0; tx <= x1; tx++) {
            s32 column = mod_small(tx, p->columns), slot = row * p->columns + column;
            s32 index = ty * width + tx;
            MapTileData *tile = &tiles[index];
            PolyFT4 *prim;
            s32 ready = 1, sx = p->slot_x[column], sy = p->slot_y[row];
            s32 page_x = sx & ~63, u = (sx - page_x) << 1, v = sy & 255, h = t;
            s32 left, top;

            if (tile->tileId != -1 && p->resident[slot] != index) {
                if (budget > 0 && tile->imagePtr) {
                    upload(p, tile, column, row);
                    p->resident[slot] = (s8)index;
                    budget--;
                } else {
                    ready = 0;
                }
            }
            if (v + h > 255)
                h = 255 - v;
            prim = (PolyFT4 *)GsGetWorkBase();
            SetPolyFT4(prim);
            if (tile->tileId == -1 || !ready) {
                prim->r0 = prim->g0 = prim->b0 = 0;
            } else {
                prim->r0 = prim->g0 = prim->b0 = 0x80;
            }
            left = tile->posX - camera_x - ox;
            top = tile->posY - camera_y - oy;
            prim->x0 = prim->x2 = (s16)scaled(left, centre_x - ox, scale);
            prim->x1 = prim->x3 = (s16)scaled(left + 128, centre_x - ox, scale);
            prim->y0 = prim->y1 = (s16)scaled(top, centre_y - oy, scale);
            prim->y2 = prim->y3 = (s16)scaled(top + 128, centre_y - oy, scale);
            prim->u0 = prim->u2 = (u8)u;
            prim->u1 = prim->u3 = (u8)(u + t);
            prim->v0 = prim->v1 = (u8)v;
            prim->v2 = prim->v3 = (u8)(v + h);
            prim->clut = (u16)tile->clut;
            prim->tpage = (u16)(0x80 | (page_x >> 6) | ((sy >> 8) << 4)); /* 8-bit, blend 0 */
            AddPrim(ot, prim);
            if (!p->drawn++)
                p->first_prim = (u32)prim;
            GsSetWorkBase(prim + 1);
        }
    }
}

/* Fixed framing entry; never writes VIEWPORT_DISTANCE, used by mapToWorldPosition/game logic. */
void fixed_set_projection(s32 distance)
{
    s32 ox = AT(DRAWING_OFFSET_X, s32), oy = AT(DRAWING_OFFSET_Y, s32);
    s32 scale = PARAMS->scale;
    s32 cx = fixed_anchor(AT(CAMERA_X, s16), AT(MAP_WIDTH, s8) * 128, 320, scale);
    s32 cy = fixed_anchor(AT(CAMERA_Y, s16), AT(MAP_HEIGHT, s8) * 128, 240, scale);
    GsSetProjection((distance * scale) >> 8);
    FIXED->geom_x = (cx - ox) * (256 - scale) << 8;
    FIXED->geom_y = (cy - oy) * (256 - scale) << 8;
    set_geom_fine(FIXED->geom_x, FIXED->geom_y);
}

/* Sprites and effects size themselves from VIEWPORT_DISTANCE while drawing. Game logic keeps the real value. */
void fixed_render_objects(void)
{
    s32 native = AT(VIEWPORT_DISTANCE, s32);
    FIXED->native_distance = native;
    FIXED->drawing = 1;
    AT(VIEWPORT_DISTANCE, s32) = (native * PARAMS->scale) >> 8;
    renderObjects();
    AT(VIEWPORT_DISTANCE, s32) = native;
    FIXED->drawing = 0;
}

typedef s32 (*NativeFn)(s32, s32, s32);

/* The camera follows the player by how far they moved on screen. Measured through the wider projection that reads
   half the distance, and the camera falls behind until the player has walked out of the frame. */
static s32 native_call(s32 index, s32 a, s32 b, s32 c)
{
    s32 native = FIXED->drawing ? FIXED->native_distance : AT(VIEWPORT_DISTANCE, s32);
    s32 result;
    GsSetProjection(native);
    set_geom_fine(0, 0);
    result = ((NativeFn)(void *)FIXED->native_entries[index])(a, b, c);
    GsSetProjection((native * PARAMS->scale) >> 8);
    set_geom_fine(FIXED->geom_x, FIXED->geom_y);
    return result;
}

s32 fixed_native_0(s32 a, s32 b, s32 c) { return native_call(0, a, b, c); }
s32 fixed_native_1(s32 a, s32 b, s32 c) { return native_call(1, a, b, c); }
s32 fixed_native_2(s32 a, s32 b, s32 c) { return native_call(2, a, b, c); }
s32 fixed_native_3(s32 a, s32 b, s32 c) { return native_call(3, a, b, c); }
s32 fixed_native_4(s32 a, s32 b, s32 c) { return native_call(4, a, b, c); }

/* handleBattleStart brings every Digimon that's on screen into the fight, and the fight is on the game's own camera.
   Only those its view can show may join, not everyone in the wider frame. */
s32 fixed_native_5(s32 a, s32 b, s32 c)
{
    u8 **entities = (u8 **)ENTITY_TABLE;
    s32 native = FIXED->drawing ? FIXED->native_distance : AT(VIEWPORT_DISTANCE, s32);
    s32 i;
    GsSetProjection(native);
    set_geom_fine(0, 0);
    for (i = 2; i < 10; i++)
        if (entities[i] && entities[i][ENTITY_IS_ON_MAP])
            entities[i][ENTITY_IS_ON_SCREEN] = entityIsOffScreen(entities[i], 320, 240) ^ 1;
    return native_call(5, a, b, c);
}

/* A rotated or scaled sprite goes through RotTransPers4, which adds the GTE offset to coordinates that are already
   on screen: HUD pieces would drift with the camera, and world sprites would get the offset twice. */
void fixed_sort_sprite(void *sprite, void *ot, s32 priority)
{
    set_geom_fine(0, 0);
    ((SortSpriteFn)(void *)FIXED->sprite_entry)(sprite, ot, priority);
    set_geom_fine(FIXED->geom_x, FIXED->geom_y);
}

void fixed_set_foreground(PolyFT4 *prim, s32 x, s32 y, s32 w, s32 h)
{
    s32 scale = PARAMS->scale;
    s32 ox = AT(GPU_OFFSET_X, s32), oy = AT(GPU_OFFSET_Y, s32); /* see zoom_render_tiles */
    s32 camera_x = AT(CAMERA_X, s16) + AT(DRAWING_OFFSET_X, s32) - ox;
    s32 camera_y = AT(CAMERA_Y, s16) + AT(DRAWING_OFFSET_Y, s32) - oy;
    s32 cx = fixed_anchor(camera_x, AT(MAP_WIDTH, s8) * 128, 320, scale) - ox;
    s32 cy = fixed_anchor(camera_y, AT(MAP_HEIGHT, s8) * 128, 240, scale) - oy;
    prim->x0 = prim->x2 = (s16)scaled(x, cx, scale);
    prim->x1 = prim->x3 = (s16)scaled(x + w, cx, scale);
    prim->y0 = prim->y1 = (s16)scaled(y, cy, scale);
    prim->y2 = prim->y3 = (s16)scaled(y + h, cy, scale);
}
