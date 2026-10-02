from collector.collection_profile import load_profile


def test_default_collection_profile_enables_both_mvp_sources():
    profile = load_profile()

    assert set(profile["enabled_sources"]) == {"seek", "linkedin"}
