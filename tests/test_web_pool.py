"""A cut's pool: every picture it saw, what became of each, and the owner's word on it."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from immich_memories.analysis.sources.snapshot import SNAPSHOT_NAME, source_payload
from immich_memories.api.models import AssetType, Person
from tests.conftest import make_asset
from tests.web_api_fixtures import api_client, config_in, save_run, selection_trace

RUN = "20260927_100000_f00d"


def _cut_with_pool(tmp_path):
    config = config_in(tmp_path)
    attempt = save_run(config, RUN, trace=selection_trace())
    pool = [
        make_asset("woods-9", file_created_at=datetime(2024, 6, 15, tzinfo=UTC)),
        make_asset("garden-1", file_created_at=datetime(2024, 6, 8, tzinfo=UTC)),
        make_asset("lake-1", file_created_at=datetime(2024, 6, 21, tzinfo=UTC)),
    ]
    (attempt / SNAPSHOT_NAME).write_text(json.dumps(source_payload(pool), default=str))
    return config, api_client(config)


def test_the_pool_lists_what_the_cut_saw_in_capture_order_with_each_fate(tmp_path):
    _config, client = _cut_with_pool(tmp_path)

    pool = client.get(f"/api/v1/runs/{RUN}/pool").json()

    assert pool["total"] == 3
    assert [item["asset_id"] for item in pool["items"]] == ["garden-1", "woods-9", "lake-1"]
    garden, woods, _lake = pool["items"]
    assert garden["in_cut"] is True and woods["in_cut"] is False
    assert "a near duplicate of the path shot" in woods["fate"]
    assert woods["hold"] == {"decision": None, "reasons": [], "can_clear": False}


def test_never_use_is_the_owner_s_word_across_runs_and_undo_forgets_it(tmp_path):
    config, client = _cut_with_pool(tmp_path)

    decided = client.post("/api/v1/pictures/woods-9/decision", json={"action": "never_use"})
    pool = client.get(f"/api/v1/runs/{RUN}/pool").json()["items"]
    forgotten = client.post("/api/v1/pictures/woods-9/decision", json={"action": "forget"})

    assert decided.status_code == 200 and decided.json()["decision"] == "never_use"
    assert next(i for i in pool if i["asset_id"] == "woods-9")["hold"]["decision"] == "never_use"
    assert forgotten.json()["decision"] is None


def test_the_pool_opens_on_this_memory_s_own_pictures_and_counts_the_rest(tmp_path):
    config = config_in(tmp_path)
    trace = selection_trace()
    # The first pass saw it; the editor, given only this memory's material, never did.
    trace.editorial_passes[0] = trace.editorial_passes[0].__class__(
        **{
            **vars(trace.editorial_passes[0]),
            "input_ids": ("garden-1", "lake-1", "woods-9", "attic-2"),
        }
    )
    attempt = save_run(config, RUN, trace=trace)
    pool = [
        make_asset("garden-1", file_created_at=datetime(2024, 6, 8, tzinfo=UTC)),
        make_asset("attic-2", file_created_at=datetime(2024, 6, 9, tzinfo=UTC)),
    ]
    (attempt / SNAPSHOT_NAME).write_text(json.dumps(source_payload(pool), default=str))

    client = api_client(config)
    garden, attic = client.get(f"/api/v1/runs/{RUN}/pool").json()["items"]

    assert garden["reachable"] is True
    assert attic["reachable"] is False
    assert attic["fate"].startswith("Outside this memory")
    # The page opens on this memory's own pictures, and says how many it left aside.
    mine = client.get(f"/api/v1/runs/{RUN}/pool", params={"reachable_only": True}).json()
    assert [item["asset_id"] for item in mine["items"]] == ["garden-1"]
    assert (mine["total"], mine["outside"]) == (1, 1)


def test_the_pool_s_ticks_are_the_owner_s_last_pass_saved_as_a_revision_without_a_recut(tmp_path):
    _config, client = _cut_with_pool(tmp_path)

    saved = client.post(
        f"/api/v1/runs/{RUN}/revisions", json={"added": ["woods-9"], "removed": ["lake-1"]}
    )
    refused = client.post(f"/api/v1/runs/{RUN}/revisions", json={"added": ["a-stranger"]})

    assert saved.status_code == 201
    assert (saved.json()["added"], saved.json()["removed"]) == (["woods-9"], ["lake-1"])
    assert refused.status_code == 422 and "not in this cut's pool" in refused.json()["detail"]
    assert client.post(f"/api/v1/runs/{RUN}/recut", json={}).status_code in {404, 405}


def test_one_picture_stored_twice_is_one_tile_its_full_size_file(tmp_path):
    """A shared album's downscale of a camera file is the same picture (#1462)."""
    config = config_in(tmp_path)
    attempt = save_run(config, RUN, trace=selection_trace())
    taken = datetime(2024, 6, 8, 11, 20, 40, 702000, tzinfo=UTC)
    original = make_asset("garden-1", file_created_at=taken, original_file_name="IMG_5213.HEIC")
    album = make_asset("album-copy", file_created_at=taken, original_file_name="IMG_5213.HEIC")
    original.width, original.height, album.width, album.height = 3024, 4032, 1536, 2048
    (attempt / SNAPSHOT_NAME).write_text(json.dumps(source_payload([album, original]), default=str))

    pool = api_client(config).get(f"/api/v1/runs/{RUN}/pool").json()

    assert [item["asset_id"] for item in pool["items"]] == ["garden-1"]
    assert pool["total"] == 1


def test_an_explicit_pool_tick_overrides_never_use_for_a_revision_without_forgetting_it(tmp_path):
    _config, client = _cut_with_pool(tmp_path)
    client.post("/api/v1/pictures/woods-9/decision", json={"action": "never_use"})

    saved = client.post(f"/api/v1/runs/{RUN}/revisions", json={"added": ["woods-9"]})
    pool = client.get(f"/api/v1/runs/{RUN}/pool").json()["items"]

    assert saved.status_code == 201
    assert saved.json()["added"] == ["woods-9"]
    assert (
        next(item for item in pool if item["asset_id"] == "woods-9")["hold"]["decision"]
        == "never_use"
    )


def _mixed_pool(tmp_path):
    """Five pictures over a week: a photo, a favourite photo, a video, a live photo, a video."""
    config = config_in(tmp_path)
    attempt = save_run(config, RUN, trace=selection_trace())
    ada, bo = Person(id="p-ada", name="Ada"), Person(id="p-bo", name="Bo")
    pool = [
        make_asset("garden-1", file_created_at=datetime(2024, 6, 8, 10, tzinfo=UTC)),
        make_asset("woods-9", file_created_at=datetime(2024, 6, 9, 10, tzinfo=UTC), is_favorite=True),
        make_asset("lake-1", file_created_at=datetime(2024, 6, 10, 10, tzinfo=UTC)),
        make_asset("path-2", file_created_at=datetime(2024, 6, 11, 10, tzinfo=UTC)),
        make_asset("hill-3", file_created_at=datetime(2024, 6, 12, 10, tzinfo=UTC)),
    ]
    by_id = {asset.id: asset for asset in pool}
    for photo in ("garden-1", "woods-9", "path-2"):
        by_id[photo].type = AssetType.IMAGE
    by_id["path-2"].live_photo_video_id = "path-2-motion"
    by_id["garden-1"].people = [ada]
    by_id["woods-9"].people = [ada, bo]
    by_id["lake-1"].people = [bo]
    (attempt / SNAPSHOT_NAME).write_text(json.dumps(source_payload(pool), default=str))
    return api_client(config)


def _ids(client, **params):
    pool = client.get(f"/api/v1/runs/{RUN}/pool", params=params).json()
    return [item["asset_id"] for item in pool["items"]], pool


def test_the_pool_can_be_narrowed_to_photos_videos_or_live_photos(tmp_path):
    client = _mixed_pool(tmp_path)

    photos, _ = _ids(client, kind="photo")
    films, _ = _ids(client, kind="video")
    both, _ = _ids(client, kind=["photo", "live"])

    assert photos == ["garden-1", "woods-9"]
    assert films == ["lake-1", "hill-3"]
    assert both == ["garden-1", "woods-9", "path-2"]


def test_the_pool_can_show_only_favourites(tmp_path):
    client = _mixed_pool(tmp_path)

    favourites, pool = _ids(client, favourite=True)

    assert favourites == ["woods-9"]
    assert pool["total"] == 1


def test_the_pool_can_be_cut_to_a_window_of_days_both_ends_included(tmp_path):
    client = _mixed_pool(tmp_path)

    window, pool = _ids(client, taken_from="2024-06-09", taken_to="2024-06-11")
    from_only, _ = _ids(client, taken_from="2024-06-11")
    to_only, _ = _ids(client, taken_to="2024-06-08")

    assert window == ["woods-9", "lake-1", "path-2"] and pool["total"] == 3
    assert from_only == ["path-2", "hill-3"]
    assert to_only == ["garden-1"]


def test_the_pool_can_be_filtered_by_person_and_lists_who_is_in_it(tmp_path):
    client = _mixed_pool(tmp_path)

    ada, pool = _ids(client, person="p-ada")
    either, _ = _ids(client, person=["p-ada", "p-bo"])

    assert ada == ["garden-1", "woods-9"]
    assert either == ["garden-1", "woods-9", "lake-1"]
    # The choices are the whole pool's people, not just the filtered page's, most pictures first.
    assert [(p["id"], p["name"], p["count"]) for p in pool["people"]] == [
        ("p-ada", "Ada", 2),
        ("p-bo", "Bo", 2),
    ]
    assert pool["items"][1]["people"] == ["p-ada", "p-bo"]


def test_filters_combine_and_page_through_the_narrowed_list(tmp_path):
    client = _mixed_pool(tmp_path)

    only, pool = _ids(client, kind="photo", person="p-bo", taken_from="2024-06-09")
    second_page, paged = _ids(client, kind=["photo", "video", "live"], offset=3, limit=2)

    assert only == ["woods-9"] and pool["total"] == 1
    assert second_page == ["path-2", "hill-3"] and paged["total"] == 5
