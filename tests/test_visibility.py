import pytest

from orca_talkback.visibility import (
    Window,
    orca_is_visible,
    orca_window_visible,
    visible_fraction,
)


def window(owner, left=0, top=0, width=1000, height=800, layer=0, alpha=1.0):
    return Window(owner=owner, layer=layer, left=left, top=top, width=width, height=height, alpha=alpha)


def test_orca_alone_is_visible():
    assert orca_window_visible([window("Orca")])


def test_orca_on_another_screen_is_visible_while_another_app_is_in_front():
    browser = window("Google Chrome", left=0, top=0)
    orca = window("Orca", left=1000, top=-1080, width=1920, height=1080)
    assert orca_window_visible([browser, orca])


def test_orca_fully_covered_by_a_browser_is_not_visible():
    browser = window("Google Chrome", left=0, top=0, width=1920, height=1080)
    orca = window("Orca", left=100, top=100, width=800, height=600)
    assert not orca_window_visible([browser, orca])


def test_orca_mostly_covered_still_counts_when_a_strip_shows():
    browser = window("Google Chrome", left=0, top=0, width=800, height=800)
    orca = window("Orca", left=0, top=0, width=1000, height=800)
    assert visible_fraction(orca, [browser]) == pytest.approx(0.2, abs=0.05)
    assert orca_window_visible([browser, orca])


def test_a_sliver_of_orca_does_not_count():
    browser = window("Google Chrome", left=0, top=0, width=980, height=800)
    orca = window("Orca", left=0, top=0, width=1000, height=800)
    assert not orca_window_visible([browser, orca])


def test_windows_behind_orca_do_not_cover_it():
    orca = window("Orca")
    browser = window("Google Chrome")
    assert orca_window_visible([orca, browser])


def test_menu_bar_dock_tooltips_and_transparent_windows_are_ignored():
    menu_bar = window("Window Server", width=3000, height=3000, layer=25)
    transparent = window("Overlay", width=3000, height=3000, alpha=0.0)
    tooltip = window("Google Chrome", width=40, height=20)
    assert orca_window_visible([menu_bar, transparent, tooltip, window("Orca")])


def test_no_orca_window_means_not_visible():
    assert not orca_window_visible([window("Google Chrome")])
    assert not orca_window_visible([])


def test_unknown_window_list_counts_as_visible():
    assert orca_is_visible(lambda: None)


def test_a_failed_read_counts_as_visible():
    def broken():
        raise OSError("no CoreGraphics")

    assert orca_is_visible(broken)


def test_reads_the_window_list_through_the_given_reader():
    assert not orca_is_visible(lambda: [window("Google Chrome", width=3000, height=3000), window("Orca")])
