import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'robot-runtime/python'))
from reference_cloud import SurveyCloud, saved_trail
from reference_service import capture_parameters


class OverviewTests(unittest.TestCase):
    def test_retains_old_region_after_delay_without_browser_and_rejects_stale_pose(self):
        c = SurveyCloud('a', 50)
        tilt = np.array([[0.,0.,1.,0.],[0.,1.,0.,0.],[-1.,0.,0.,0.],[0.,0.,0.,1.]])
        xyz = np.column_stack((np.zeros(80), np.arange(80) / 10, np.ones(80)))
        for i in range(30):
            tf = tilt.copy(); tf[2,3] = -i
            f = dict(stamp_ns=(i+1)*1000000000, odom_imu=tf, body_points=xyz)
            c.add(f, None); c.add(f, np.eye(4)); c.add(f, np.eye(4))
        snap = c.snapshot()
        self.assertLessEqual(len(snap['xyz'])//3,50)
        self.assertLess(min(snap['xyz'][::3]), 4); self.assertGreater(max(snap['xyz'][::3]),25)
        self.assertEqual(len(snap['trail']),90); self.assertEqual(snap['trail'][-3:-1],[29.,0.])
        self.assertIsNone(snap['pose']); self.assertEqual(SurveyCloud('b').snapshot()['xyz'],[])

    def test_csv_rejects_wrong_count_or_reversed_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'trajectory.csv'; header='time_s,x,y,z,qx,qy,qz,qw\n'
            p.write_text(header+'1,2,3,0,0,0,0,1\n2,3,4,0,0,0,0,1\n')
            self.assertEqual(saved_trail(p,2),[2,3,1,3,4,2]); self.assertRaises(ValueError,saved_trail,p,3)
            p.write_text(header+'2,2,3,0,0,0,0,1\n1,3,4,0,0,0,0,1\n')
            self.assertRaises(ValueError,saved_trail,p,2)

    def test_capture_reduces_only_unused_publications(self):
        base = dict(mapping={'extrinsic_R':[1]}, publish={'map_en':True,'scan_publish_en':True}, maximum_lidar_backlog_s=1)
        load = capture_parameters(lambda _:base,Path('target'))
        self.assertIs(load(Path('else')),base); capture=load(Path('target'))
        self.assertFalse(capture['publish']['map_en']); self.assertTrue(base['publish']['map_en'])
        self.assertTrue(capture['publish']['scan_publish_en']); self.assertEqual(capture['mapping'],base['mapping'])
        self.assertEqual(capture['maximum_lidar_backlog_s'],1)
