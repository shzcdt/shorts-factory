import unittest

from clip_pilot.scenes import merge_and_filter_scenes


class TestMergeAndFilterScenes(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(merge_and_filter_scenes([]), [])

    def test_passes_normal_scenes_unchanged(self):
        scenes = [(0.0, 10.0), (10.0, 30.0)]
        self.assertEqual(merge_and_filter_scenes(scenes), scenes)

    def test_merges_short_scene_into_previous(self):
        scenes = [(0.0, 10.0), (10.0, 12.0), (12.0, 30.0)]
        result = merge_and_filter_scenes(scenes, min_scene_seconds=5.0)
        self.assertEqual(result, [(0.0, 12.0), (12.0, 30.0)])

    def test_merges_short_first_scene_into_next(self):
        scenes = [(0.0, 2.0), (2.0, 30.0)]
        result = merge_and_filter_scenes(scenes, min_scene_seconds=5.0)
        self.assertEqual(result, [(0.0, 30.0)])

    def test_splits_long_scene_into_chunks(self):
        scenes = [(0.0, 90.0)]
        result = merge_and_filter_scenes(scenes, max_clip_seconds=60.0)
        self.assertEqual(result, [(0.0, 45.0), (45.0, 90.0)])

    def test_split_chunks_respect_max(self):
        scenes = [(0.0, 100.0)]
        result = merge_and_filter_scenes(scenes, max_clip_seconds=30.0)
        for start, end in result:
            self.assertLessEqual(end - start, 30.0 + 1e-6)
        self.assertAlmostEqual(result[0][0], 0.0)
        self.assertAlmostEqual(result[-1][1], 100.0)


if __name__ == "__main__":
    unittest.main()
