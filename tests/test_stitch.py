from vbtool import stitch


def test_scores_before_each_point():
    pts = [{"extra": {"winner": "near"}}, {"extra": {"winner": "far"}}, {"extra": {}},
           {"extra": {"winner": "near"}}, {}]
    assert stitch.scores_before(pts) == [(0, 0), (1, 0), (1, 1), (1, 1), (2, 1)]


def words(parts):
    return " ".join(p[1] for p in parts if p[0] == "text")


def test_labels():
    p = {"index": 1, "start_s": 24.4}
    assert words(stitch.clip_parts(p)) == "Point 1 0:24"
    assert words(stitch.stitched_parts(p, (9, 8))) == "Point 1 Bumps 9 \u2013 8 Opponents"


def test_label_image_matches_mask(tmp_path):
    from PIL import Image
    p = {"index": 12, "start_s": 384}
    label, mask = tmp_path / "label.png", tmp_path / "mask.png"
    w, h = stitch.make_label(stitch.stitched_parts(p, (9, 8)), 720, label, mask)
    assert w % 2 == 0 and h % 2 == 0
    assert Image.open(label).size == Image.open(mask).size == (w, h)
    assert Image.open(label).getpixel((0, 0))[3] == 0      # rounded corner is see-through
    assert Image.open(mask).getpixel((w // 2, h // 2)) == 255
