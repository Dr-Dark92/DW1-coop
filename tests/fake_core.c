/* A tiny libretro core for tests: no emulation, just predictable frames, audio, options and states. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

struct retro_system_info { const char *library_name, *library_version, *valid_extensions; bool need_fullpath, block_extract; };
struct retro_game_geometry { unsigned base_width, base_height, max_width, max_height; float aspect_ratio; };
struct retro_system_timing { double fps, sample_rate; };
struct retro_system_av_info { struct retro_game_geometry geometry; struct retro_system_timing timing; };
struct retro_game_info { const char *path; const void *data; size_t size; const char *meta; };
struct retro_variable { const char *key, *value; };

typedef bool (*env_t)(unsigned, void *);
typedef void (*video_t)(const void *, unsigned, unsigned, size_t);
typedef void (*sample_t)(int16_t, int16_t);
typedef size_t (*batch_t)(const int16_t *, size_t);
typedef void (*poll_t)(void);
typedef int16_t (*state_t)(unsigned, unsigned, unsigned, unsigned);

#define W 8
#define H 4
#define SAMPLES 4

static env_t env; static video_t video; static batch_t batch; static poll_t poll; static state_t input;
static uint32_t pixels[W * H];
static uint8_t save_ram[16];
static uint8_t system_ram[0x200000]; /* laid out like the USA game's RAM where tests need it */
static uint32_t counter, buttons_seen;
static char system_dir[512], option[64], first_byte;

EXPORT unsigned retro_api_version(void) { return 1; }
EXPORT void retro_set_environment(env_t cb) { env = cb; }
EXPORT void retro_set_video_refresh(video_t cb) { video = cb; }
EXPORT void retro_set_audio_sample(sample_t cb) { (void)cb; }
EXPORT void retro_set_audio_sample_batch(batch_t cb) { batch = cb; }
EXPORT void retro_set_input_poll(poll_t cb) { poll = cb; }
EXPORT void retro_set_input_state(state_t cb) { input = cb; }

EXPORT void retro_init(void) {
    static const struct retro_variable vars[] = {{"fake_option", "Fake option; first|second"}, {NULL, NULL}};
    unsigned format = 1; /* XRGB8888 */
    const char *dir = NULL;
    counter = 0; memset(save_ram, 0, sizeof save_ram);
    env(10, &format);
    env(16, (void *)vars);
    if (env(9, &dir) && dir) snprintf(system_dir, sizeof system_dir, "%s", dir);
}
EXPORT void retro_deinit(void) {}
EXPORT void retro_get_system_info(struct retro_system_info *info) {
    info->library_name = "Fake Core"; info->library_version = "1.0"; info->valid_extensions = "cue|bin";
    info->need_fullpath = true; info->block_extract = false;
}
EXPORT void retro_get_system_av_info(struct retro_system_av_info *info) {
    info->geometry = (struct retro_game_geometry){W, H, W, H, 2.0f};
    info->timing = (struct retro_system_timing){60.0, 44100.0};
}
EXPORT void retro_set_controller_port_device(unsigned port, unsigned device) { (void)port; (void)device; }
EXPORT void retro_reset(void) { counter = 0; }
EXPORT void retro_run(void) {
    int16_t audio[SAMPLES * 2];
    poll();
    buttons_seen = (uint16_t)input(0, 1, 0, 256);
    for (int i = 0; i < W * H; i++) pixels[i] = 0xFF000000u | counter;
    for (int i = 0; i < SAMPLES * 2; i++) audio[i] = (int16_t)counter;
    save_ram[0] = (uint8_t)counter;
    {   /* CURRENT_SCREEN = 7; TAMER_ENTITY.posData -> 0x80100000 with location (counter * 10, 0, -250) */
        uint32_t pointer = 0x80100000u; int32_t location[3] = {(int32_t)counter * 10, 0, -250};
        system_ram[0x134DA8] = 7;
        memcpy(system_ram + 0x15576C + 4, &pointer, 4);
        memcpy(system_ram + 0x100000 + 120, location, sizeof location);
        system_ram[0x1AF398 + 50 + 52 * 100] = 113; /* exit through warp slot 3 at tile (50, 52) */
        {   /* ENTITY_TABLE[2] -> an on-map NPC of type 43 at (-162, 0, -2352), tile (48, 73) */
            uint32_t npc = 0x80110000u, npc_position = 0x80120000u; int32_t type = 43;
            int32_t npc_location[3] = {-162, 0, -2352};
            memcpy(system_ram + 0x12F344 + 2 * 4, &npc, 4);
            memcpy(system_ram + 0x110000, &type, 4);
            memcpy(system_ram + 0x110000 + 4, &npc_position, 4);
            system_ram[0x110000 + 52] = 1;
            memcpy(system_ram + 0x120000 + 120, npc_location, sizeof npc_location);
        }
    }
    save_ram[1] = (uint8_t)buttons_seen;
    video(counter % 2 ? NULL : pixels, W, H, W * 4); /* odd frames are dupes */
    batch(audio, SAMPLES);
    counter++;
}
EXPORT size_t retro_serialize_size(void) { return 8; }
EXPORT bool retro_serialize(void *data, size_t size) {
    if (size < 8) return false;
    memcpy(data, "FAKE", 4); memcpy((char *)data + 4, &counter, 4); return true;
}
EXPORT bool retro_unserialize(const void *data, size_t size) {
    if (size != 8 || memcmp(data, "FAKE", 4)) return false;
    memcpy(&counter, (const char *)data + 4, 4); return true;
}
EXPORT bool retro_load_game(const struct retro_game_info *game) {
    struct retro_variable var = {"fake_option", NULL};
    FILE *file = fopen(game->path, "rb");
    if (!file) return false;
    first_byte = (char)fgetc(file); fclose(file);
    if (env(15, &var) && var.value) snprintf(option, sizeof option, "%s", var.value);
    return first_byte != 'X'; /* a game starting with X "fails to boot" */
}
EXPORT bool retro_load_game_special(unsigned type, const struct retro_game_info *info, size_t num) {
    (void)type; (void)info; (void)num; return false;
}
EXPORT void retro_unload_game(void) {}
EXPORT unsigned retro_get_region(void) { return 0; }
EXPORT void *retro_get_memory_data(unsigned id) { return id == 0 ? (void *)save_ram : id == 2 ? (void *)system_ram : NULL; }
EXPORT size_t retro_get_memory_size(unsigned id) { return id == 0 ? sizeof save_ram : id == 2 ? sizeof system_ram : 0; }
EXPORT void retro_cheat_reset(void) {}
EXPORT void retro_cheat_set(unsigned index, bool enabled, const char *code) { (void)index; (void)enabled; (void)code; }

/* Test probes. */
EXPORT const char *fake_system_dir(void) { return system_dir; }
EXPORT const char *fake_option_value(void) { return option; }
