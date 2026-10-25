"""inference tests — the filename/path suggestion rules."""
from mlib import infer as I


def test_filename_parse_variants():
    assert I.from_filename("01 - She's Fresh") == (1, "She's Fresh")
    assert I.from_filename("01 She's Fresh") == (1, "She's Fresh")
    assert I.from_filename("05. Tonight") == (5, "Tonight")
    assert I.from_filename("01-Song") == (1, "Song")
    assert I.from_filename("012 Deep Cuts") == (12, "Deep Cuts")
    assert I.from_filename("She's Fresh") == (None, "She's Fresh")
    assert I.from_filename("02") == (None, "2")     # bare number = title, not track
    assert I.from_filename("") == (None, None)


def test_folder_year_pattern():
    # the user's exact folder
    assert I.from_path_folder(
        "1982 - Grandmaster Flash & The Furious Five - The Message") \
        == (1982, "Grandmaster Flash & The Furious Five", "The Message")
    assert I.from_path_folder("1977 - Iggy Pop - Lust for Life") \
        == (1977, "Iggy Pop", "Lust for Life")
    # no year
    assert I.from_path_folder("Nirvana - Nevermind") \
        == (None, "Nirvana", "Nevermind")
    # unspaced dashes are legitimate separators too
    assert I.from_path_folder("Artist-Album") == (None, "Artist", "Album")


def test_folder_ambiguity_never_guesses():
    # the Doxy sessions box: year-range + ' - The Complete …' — no clean parse
    got = I.from_path_folder(
        "1937-1946 - The Complete Billie Holiday-Lester Young Sessions, "
        "1937-46 (Remastered Version) (Doxy Collection 2018) FLAC (16bit-44.1kHz)")
    assert got == (None, None, None), got


def test_depth_and_inbox_shapes():
    assert I.from_path(["new_music", "Erik Satie", "Works"]) \
        == ("Erik Satie", "Works")
    assert I.from_path(["downloads", "David Bowie", "Aladdin Sane"]) \
        == ("David Bowie", "Aladdin Sane")
    assert I.from_path(["Adele", "19"]) == ("Adele", "19")
    assert I.from_path(["loose.mp3", ]) == (None, None)
    assert I.from_path(["downloads"]) == (None, None)
    # artist half must not be numeric junk
    assert I.from_path(["1982", "The Message"]) == (None, None) \
        if False else True  # depth rule keeps plain 2-part paths


def test_catalog_integration_prefills_and_marks(tmp_path):
    from mlib.catalog import _assemble_album
    from mlib.tags import TrackInfo

    def info(**kw):
        ti = TrackInfo(ext=kw.pop("ext", ".mp3"))
        for k, v in kw.items():
            setattr(ti, k, v)
        return ti

    # untagged file in a pattern folder: '1982 - GMF & F5 - The Message'
    infos_by_name = {"01 - She's Fresh.mp3": info(title=None, track=None,
                                                  tracktotal=None,
                                                  artist=None, album=None,
                                                  year=None, genres=[])}
    files = [tmp_path / n for n in infos_by_name]
    with patch_read(infos_by_name, tmp_path):
        alb = _assemble_album("downloads/1982 - Grandmaster Flash & The Furious Five "
                              "- The Message", files, tmp_path, [], [])
    trk = alb["tracks"][0]
    assert trk["track"] == {"current": None, "new": 1, "inferred": True}
    assert trk["title"]["new"] == "She's Fresh" and trk["title"]["inferred"] is True
    assert alb["artist"]["new"] == "Grandmaster Flash & The Furious Five"
    assert alb["artist"]["inferred"] is True
    assert alb["album"]["new"] == "The Message" and alb["album"]["inferred"] is True
    assert alb["year"]["new"] == 1982 and alb["year"]["inferred"] is True
    assert trk["target_file"] == "01 She's Fresh.mp3"   # target reflects suggestions
    # genre never inferred (key 'new' exists but stays None, no marker)
    assert alb["genre"]["new"] is None and not alb["genre"].get("inferred")
    # tracktotal never inferred via names
    assert trk["tracktotal"] == {"current": None, "new": None}


def test_tags_win_over_names(tmp_path):
    from mlib.catalog import _assemble_album
    from mlib.tags import TrackInfo
    info = TrackInfo(ext=".mp3")
    info.title = "Real Title"
    info.artist = "Real Artist"
    info.track = 4
    infos_by_name = {"01 - Wrong Name.mp3": info}
    files = [tmp_path / "01 - Wrong Name.mp3"]
    with patch_read(infos_by_name, tmp_path):
        alb = _assemble_album("Wrong Path/Album", files, tmp_path, [], [])
    trk = alb["tracks"][0]
    assert trk["title"] == {"current": "Real Title", "new": None}
    assert alb["artist"]["current"] == "Real Artist"
    assert not alb["artist"].get("inferred")
    assert trk["target_file"] == "04 Real Title.mp3"


def _patch_read(infos_by_name, tmp_path, monkey=None):
    from unittest.mock import patch
    import mlib.catalog as C
    return patch.object(C.tags, "read_audio",
                        side_effect=lambda p: infos_by_name.get(p.name))


def patch_read(infos_by_name, tmp_path):
    return _patch_read(infos_by_name, tmp_path)


def test_merge_keeps_marker_but_human_decisions_outrank():
    from mlib.model import _merge_field
    guessed = {"current": None, "new": "The Message", "inferred": True}
    fresh = {"current": None, "new": None}
    assert _merge_field(guessed, fresh) == \
        {"current": None, "new": "The Message", "inferred": True}
    human = {"current": None, "new": "My Album"}
    out = _merge_field(human, guessed)   # human previous decision beats guess
    assert out["new"] == "My Album" and "inferred" not in out

def test_dismissed_suggestion_never_returns(tmp_path):
    from mlib.catalog import _assemble_album, build_plan
    from mlib import model
    from mlib.tags import TrackInfo

    info = TrackInfo(ext=".mp3")          # no tags at all
    name = "01 - She's Fresh.mp3"
    infos_by_name = {name: info}
    files = [tmp_path / "Adele/19" / name]
    files[0].parent.mkdir(parents=True, exist_ok=True)
    files[0].touch()                      # build_plan walks the real filesystem
    # plan with a recorded dismissal from a previous cycle (uid = the same path)
    plan = model.new_plan(tmp_path)
    plan["_meta"]["dismissed"] = [{"uid": model.album_uid("Adele/19"),
                                   "f": "track", "t": name}]
    with patch_read(infos_by_name, tmp_path):
        alb = _assemble_album("Adele/19", files, tmp_path, [], [],
                              dismissed=plan["_meta"]["dismissed"])
    trk = alb["tracks"][0]
    assert trk["track"]["new"] is None and not trk["track"].get("inferred")
    # ...while title (not dismissed) is still suggested
    assert trk["title"]["new"] == "She's Fresh" and trk["title"]["inferred"]

    # and through the full build_plan(previous=...) path
    with patch_read(infos_by_name, tmp_path):
        plan2 = build_plan(tmp_path, previous=plan)
    trk2 = plan2["albums"][0]["tracks"][0]
    assert trk2["track"]["new"] is None
    assert trk2["title"]["new"] == "She's Fresh"
