from vbtool import stitch


def test_scores_before_each_point():
    pts = [{"extra": {"winner": "near"}}, {"extra": {"winner": "far"}}, {"extra": {}},
           {"extra": {"winner": "near"}}, {}]
    assert stitch.scores_before(pts) == [(0, 0), (1, 0), (1, 1), (1, 1), (2, 1)]


def test_labels():
    p = {"index": 1, "start_s": 24.4}
    assert stitch.clip_label(p) == "Point 1  |  0:24"
    assert stitch.stitched_label(p, (0, 0)) == "Point 1  |  Bumps 0 - 0 Opponents"
