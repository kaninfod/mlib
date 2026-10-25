from mlib.layout import (album_path_target, conforms_to_layout, format_file_target,
                         natural_key, pad_track, sanitize)


def test_sanitize_replaces_hostile_chars():
    assert sanitize('AC/DC: Back In Black?') == "AC_DC_ Back In Black_"
    assert sanitize('the "Best" *Hits*') == 'the _Best_ _Hits_'
    assert sanitize('C:\\music') == 'C_music'


def test_sanitize_trims_and_normalizes():
    assert sanitize('  Adele  ') == "Adele"
    assert sanitize('name...') == "name"
    assert sanitize('') == "_"
    assert sanitize(None) == "_"  # defensive: coerced to str


def test_sanitize_keeps_parens_and_spaces():
    assert sanitize('To Know You Is to Love You (single version)') == \
        'To Know You Is to Love You (single version)'


def test_pad_track():
    assert pad_track(7) == "07"
    assert pad_track(17) == "17"
    assert pad_track(120) == "120"


def test_file_target():
    assert format_file_target(8, 'To Know You Is to Love You (single version)') == \
        '08 To Know You Is to Love You (single version).mp3'
    assert format_file_target(None, 'x') is None
    assert format_file_target(3, None) is None
    assert format_file_target(3, '') is None
    assert format_file_target(2, 'AC/DC Song') == '02 AC_DC Song.mp3'


def test_album_path_target():
    assert album_path_target('B.B. King', 'His Definitive Greatest Hits') == \
        'B.B. King/His Definitive Greatest Hits'
    assert album_path_target('B.B. King', None) is None
    assert album_path_target('a:b', 'c?') == 'a_b/c_'


def test_conforms_to_layout():
    assert conforms_to_layout('08 Funkallero.mp3', 8, 'Funkallero')
    assert not conforms_to_layout('08_funkallero.mp3', 8, 'Funkallero')
    assert not conforms_to_layout('Track 1.mp3', 1, 'Track 1')


def test_natural_key():
    assert sorted(['10 x', '2 x', '01 y'], key=natural_key) == ['01 y', '2 x', '10 x']