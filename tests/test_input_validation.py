"""Malformed input boundaries; fixture reviews are automated test data only."""
import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

import leakage_audit as audit


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        Image.new('RGB', (20, 16), 'red').save(self.inputs / 'red.png')
        self.rows = [self.row('a'), self.row('b', split='test')]
        self.manifest = self.inputs / 'manifest.jsonl'
        self.write_manifest(self.rows)
        self.run_dir = self.root / 'audit'
        self.value, _ = audit.run_audit(self.manifest, self.run_dir)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def row(aid, path='red.png', split='train', **extra):
        return dict(asset_id=aid, path=path, split=split, source_group='unknown', **extra)

    def write_manifest(self, rows):
        self.manifest.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def payload(self):
        return dict(schema_version='2.0', **{k: self.value[k] for k in
                    ('manifest_digest', 'input_digest', 'config_digest')}, reviews=[dict(
                        pair_id=audit.pair_id('a', 'b'), decision='unsure', evidence='Automated test',
                        reviewer='AUTOMATED_INPUT_TEST', origin='construction_truth_fixture')], manual_pairs=[])

    def cli(self, *args):
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            status = audit.main(list(map(str, args)))
        self.assertNotIn('Traceback', stderr.getvalue())
        return status, stderr.getvalue()

    def bad_reviews(self, payload, index):
        path = self.root / f'reviews{index}.json'
        path.write_text(json.dumps(payload))
        target = self.root / f'rejected{index}'
        before = audit.sha256(self.run_dir / 'audit.json')
        status, _ = self.cli('import-reviews', '--audit', self.run_dir, '--reviews', path, '--out', target)
        self.assertEqual(status, 2)
        self.assertEqual(audit.read_json(target / 'result.json')['status'], 'blocked')
        self.assertFalse((target / 'audit.json').exists())
        self.assertEqual(audit.sha256(self.run_dir / 'audit.json'), before)

    def mutate_bundle(self, name, mutate_result=None, mutate_audit=None):
        target = self.root / name
        shutil.copytree(self.run_dir, target)
        result = audit.read_json(target / 'result.json')
        if mutate_audit:
            value = audit.read_json(target / 'audit.json')
            mutate_audit(value)
            audit.write_json(target / 'audit.json', value)
            for item in result['artifacts']:
                if item['path'] == 'audit.json':
                    item['sha256'] = audit.sha256(target / 'audit.json')
        if mutate_result:
            mutate_result(result)
        audit.write_json(target / 'result.json', result)
        return target

    def test_bad_review_shapes_are_blocked_atomically(self):
        payloads = [[], None, 4, dict(self.payload(), reviews=[None]),
                    dict(self.payload(), reviews=[[]]), dict(self.payload(), reviews=None),
                    dict(self.payload(), manual_pairs=None), dict(self.payload(), manual_pairs=[None])]
        no_reviews = self.payload(); del no_reviews['reviews']; payloads.append(no_reviews)
        for index, payload in enumerate(payloads):
            with self.subTest(index=index):
                self.bad_reviews(payload, index)

    def test_invalid_manual_reason_ids_and_duplicates_are_blocked(self):
        cases = [dict(asset_a='a', asset_b='b', reason=reason) for reason in (None, 0, [], {}, '')]
        cases += [dict(asset_a=[], asset_b='b', reason='test'), dict(asset_a='a', asset_b='a', reason='test')]
        for index, item in enumerate(cases):
            with self.subTest(index=index):
                self.bad_reviews(dict(self.payload(), reviews=[], manual_pairs=[item]), index)
        pair = dict(asset_a='a', asset_b='b', reason='test')
        self.bad_reviews(dict(self.payload(), reviews=[], manual_pairs=[pair, pair]), 99)

    def test_bad_review_identity_decision_and_duplicate_are_blocked(self):
        for index, patch in enumerate([dict(pair_id=[]), dict(decision=[]), dict(evidence=None), dict(origin='not-declared')]):
            payload = self.payload(); payload['reviews'][0].update(patch)
            with self.subTest(index=index):
                self.bad_reviews(payload, index)
        payload = self.payload(); payload['reviews'] *= 2
        self.bad_reviews(payload, 99)

    def test_json_nonfinite_and_duplicate_keys_rejected(self):
        for value in ('NaN', 'Infinity', '-Infinity', '1e999'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'INVALID_JSON'):
                audit.strict_json('{"value":' + value + '}')
        with self.assertRaisesRegex(ValueError, 'INVALID_JSON'):
            audit.strict_json('{"asset_id":"a","asset_id":"b"}')
        self.write_manifest([self.row('a', license_note=float('nan'))])
        self.assertEqual(self.cli('audit', '--manifest', self.manifest, '--out', self.root/'nan')[0], 2)

    def test_config_types_finiteness_and_unique_splits(self):
        invalid = [dict(declared_splits=[[], 'test']), dict(declared_splits=['train', 'train']),
                   dict(declared_splits=[' ', 'test']), dict(text_jaccard_min=True),
                   dict(video_sample_interval_s=False), dict(video_sample_interval_s=10 ** 400),
                   dict(video_sample_interval_s=float('nan')), dict(image_hamming_max=True)]
        for cfg in invalid:
            with self.subTest(cfg=str(cfg)[:80]), self.assertRaisesRegex(ValueError, 'INVALID_CONFIG'):
                audit.validate_config(cfg)

    def test_csv_duplicate_header_and_wrong_width_rejected(self):
        cases = [
            'asset_id,path,split,source_group,asset_id\na,red.png,train,unknown,replaced\n',
            'asset_id,path,split,source_group\na,red.png,train,unknown,extra\n',
            'asset_id,path,split,source_group,text\na,red.png,train,unknown\n',
            'asset_id,path,split,source_group,\na,red.png,train,unknown,foo\n',
        ]
        for index, content in enumerate(cases):
            path = self.inputs/f'bad{index}.csv'; path.write_text(content)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'INVALID_CSV'):
                audit.load_manifest(path, audit.DEFAULT_CONFIG)

    def test_manifest_empty_duplicate_id_and_optional_types_rejected(self):
        cases = [[], [self.row('a'), self.row(' a ')], [self.row('a', text=0)],
                 [self.row('a', provenance={})], [self.row('a', media_type=[])],
                 [None], [dict(self.row('a'), source_group=None)]]
        for index, rows in enumerate(cases):
            self.write_manifest(rows)
            with self.subTest(index=index), self.assertRaises(ValueError):
                audit.run_audit(self.manifest, self.root/f'bad{index}')
            self.assertFalse((self.root/f'bad{index}').exists())

    def test_media_parent_absolute_and_symlink_escape_never_read(self):
        outside = self.root/'outside.png'; shutil.copyfile(self.inputs/'red.png', outside)
        (self.inputs/'link.png').symlink_to(outside)
        paths = ['../outside.png', str(outside), 'link.png', 'bad\x00.png']
        real_sha = audit.sha256
        def guarded_sha(path):
            self.assertNotEqual(Path(path), outside)
            return real_sha(path)
        self.write_manifest([self.row(f'a{i}', path) for i, path in enumerate(paths)])
        with mock.patch.object(audit, 'sha256', side_effect=guarded_sha):
            value, result = audit.run_audit(self.manifest, self.root/'escaped')
        self.assertEqual(value['summary']['planned_assets'], 4)
        self.assertEqual(value['summary']['audited_assets'], 0)
        self.assertEqual(result['quality']['verdict'], 'incomplete')
        self.assertEqual({f['code'] for f in value['failure_ledger']}, {'MEDIA_PATH_ESCAPE'})
        self.assertTrue(all(a['sha256'] is None for a in value['assets']))

    def test_zero_byte_directory_and_missing_media_remain_failures(self):
        (self.inputs/'empty.png').touch(); (self.inputs/'folder.png').mkdir()
        self.write_manifest([self.row('a', 'empty.png'), self.row('b', 'empty.png', 'test'),
                             self.row('c', 'folder.png'), self.row('d', 'missing.png')])
        value, result = audit.run_audit(self.manifest, self.root/'empty')
        self.assertEqual(value['summary']['planned_assets'], 4)
        self.assertEqual(value['summary']['audited_assets'], 0)
        self.assertEqual(len(value['failure_ledger']), 4)
        self.assertEqual(audit.accepted_edges(value), [])
        self.assertEqual(result['quality']['verdict'], 'incomplete')

    def test_output_file_and_existing_run_are_preserved(self):
        output = self.root/'keep'; output.write_text('untouched')
        self.assertEqual(self.cli('audit', '--manifest', self.manifest, '--out', output)[0], 2)
        self.assertEqual(output.read_text(), 'untouched')
        before = audit.sha256(self.run_dir/'result.json')
        self.assertEqual(self.cli('audit', '--manifest', self.manifest, '--out', self.run_dir)[0], 2)
        self.assertEqual(audit.sha256(self.run_dir/'result.json'), before)

    def test_nested_output_and_future_input_collision_do_not_write(self):
        output = self.run_dir/'nested'
        self.assertEqual(self.cli('propose-split', '--audit', self.run_dir, '--out', output)[0], 2)
        self.assertFalse(output.exists())
        output = self.inputs/'new'
        self.write_manifest([self.row('a'), self.row('b', 'new/evidence/future.jpg')])
        self.assertEqual(self.cli('audit', '--manifest', self.manifest, '--out', output)[0], 2)
        self.assertFalse(output.exists())

    def test_artifact_omission_and_duplicate_entries_blocked(self):
        mutations = [lambda r: r.update(artifacts=[]), lambda r: r['artifacts'].pop(0),
                     lambda r: r['artifacts'].append(r['artifacts'][0])]
        for index, mutation in enumerate(mutations):
            target = self.mutate_bundle(f'bundle{index}', mutate_result=mutation)
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'ARTIFACT_MANIFEST'):
                audit.load_audit(target)

    def test_artifact_parent_and_symlink_escape_blocked(self):
        outside = self.root/'outside'; outside.write_text('private')
        for index, name in enumerate(['../outside', str(outside), 'outside-link']):
            target = self.mutate_bundle(f'bundle{index}', mutate_result=lambda r, name=name:
                                       r['artifacts'].append(dict(path=name, sha256=audit.sha256(outside))))
            if name == 'outside-link':
                (target/name).symlink_to(outside)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'ARTIFACT_PATH_ESCAPE'):
                audit.load_audit(target)

    def test_result_binding_and_rehashed_input_tamper_blocked(self):
        target = self.mutate_bundle('wrong-result', mutate_result=lambda r:r.update(input_digest='0'*64))
        with self.assertRaisesRegex(ValueError, 'AUDIT_BINDING_MISMATCH'):
            audit.load_audit(target)
        target = self.mutate_bundle('wrong-input', mutate_audit=lambda a:a['assets'][0].update(sha256='1'*64))
        with self.assertRaisesRegex(ValueError, 'AUDIT_BINDING_MISMATCH'):
            audit.load_audit(target)

    def test_rehashed_summary_graph_and_ledger_tamper_blocked(self):
        mutations = [lambda a:a['summary'].update(planned_assets=99),
                     lambda a:a['candidate_edges'][0].update(asset_a='missing'),
                     lambda a:a.update(failure_ledger=[None]),
                     lambda a:a.update(confirmed_components=[])]
        for index, mutation in enumerate(mutations):
            target = self.mutate_bundle(f'tamper{index}', mutate_audit=mutation)
            with self.subTest(index=index), self.assertRaises(ValueError):
                audit.load_audit(target)

    def test_scan_required_cannot_be_set_with_truthy_string(self):
        target = self.mutate_bundle('scan-string', mutate_audit=lambda a:a.update(pair_scan_completed='false'))
        with self.assertRaisesRegex(ValueError, 'AUDIT_SCHEMA'):
            audit.load_audit(target)
        with self.assertRaisesRegex(ValueError, 'PAIR_SCAN_REQUIRED'):
            audit.require_pair_scan(dict(pair_scan_completed='false', provenance=dict(operation='audit')))

    def test_nonfinite_ratios_are_blocked(self):
        for index, ratios in enumerate(['nan,0.15,0.15', 'inf,0.15,0.15', '0.7,0.3', '0,0.5,0.5']):
            target = self.root/f'proposal{index}'
            status, _ = self.cli('propose-split', '--audit', self.run_dir, '--ratios', ratios, '--out', target)
            self.assertEqual(status, 2)
            self.assertFalse((target/'split_proposal.csv').exists())

    def test_invalid_video_count_releases_capture(self):
        for count in (float('nan'), float('inf'), -1):
            capture = mock.Mock(); capture.isOpened.return_value = True
            capture.get.side_effect = lambda prop: 30 if prop == audit.cv2.CAP_PROP_FPS else count
            with mock.patch.object(audit.cv2, 'VideoCapture', return_value=capture):
                with self.assertRaisesRegex(ValueError, 'VIDEO_FRAME_COUNT_INVALID'):
                    audit.video_features('dummy.mp4', audit.DEFAULT_CONFIG, self.root/'thumb.jpg')
            capture.release.assert_called_once()

    def test_valid_review_ignores_unbound_extra_files_and_preserves_groups(self):
        (self.run_dir/'evidence/private.txt').write_text('unbound')
        (self.run_dir/'manifest.original.private').write_text('unbound')
        payload = self.payload(); path = self.root/'review.json'; audit.write_json(path, payload)
        target = self.root/'reviewed'
        reviewed, _ = audit.import_reviews(self.run_dir, path, target)
        self.assertFalse((target/'evidence/private.txt').exists())
        self.assertFalse((target/'manifest.original.private').exists())
        audit.load_audit(target)
        rows, _ = audit.propose_split(target, self.root/'proposal')
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r['proposed_split'] for r in rows}), 1)
        self.assertEqual(reviewed['input_digest'], self.value['input_digest'])


if __name__ == '__main__':
    unittest.main()
