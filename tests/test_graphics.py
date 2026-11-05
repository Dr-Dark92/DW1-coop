from dw1_workbench import graphics as G


def test_core_options_for_picture_settings():
    assert G.core_options() == {"beetle_psx_internal_resolution": "2x", "beetle_psx_dither_mode": "disabled"}
    native = G.core_options("Native (1x)", smooth_colours=False, steady_3d=True)
    assert native["beetle_psx_internal_resolution"] == "1x(native)" and native["beetle_psx_dither_mode"] == "1x(native)"
    assert native["beetle_psx_pgxp_mode"] == "memory only" and native["beetle_psx_pgxp_texture"] == "enabled"
    assert G.core_options("unknown")["beetle_psx_internal_resolution"] == "2x"


def test_screen_shapes_keep_the_game_shape_unless_stretched():
    assert G.screen_rects(1600, 900, 4 / 3, "4:3") == ((200, 0, 1200, 900),) * 2
    area, game = G.screen_rects(1600, 1000, 4 / 3, "16:9 fill")
    assert area == (0, 50, 1600, 900) and game == (200, 50, 1200, 900)
    assert G.screen_rects(1600, 1000, 4 / 3, "16:9 stretch") == ((0, 50, 1600, 900),) * 2


def test_explicit_four_three_does_not_inherit_beetle_corrected_aspect():
    core_aspect = 1.2190476655960083  # measured on the user's installed Beetle PSX, 640x480 game frame
    assert G.screen_rects(640, 480, core_aspect, "4:3") == ((0, 0, 640, 480),) * 2
    area, game = G.screen_rects(960, 540, core_aspect, "16:9 fill")
    assert area == (0, 0, 960, 540)
    assert game == (120, 0, 720, 540)
    corrected, _ = G.screen_rects(640, 480, core_aspect, "Core aspect")
    assert abs(corrected[2] / corrected[3] - core_aspect) < 1e-8
