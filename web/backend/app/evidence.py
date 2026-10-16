"""Read-only links between recorded localization, verification and CCT evidence.

IDs are job-scoped hashes of existing semantic IDs. Never open paths supplied
by a request or by a localization report. Missing links are explicit errors.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from csc_engine.cct_snapshot import validate_cct_snapshot
from csc_engine.refined_tbfv import _test_case_id_from_record
from csc_engine.sbfl import DEFAULT_SBFL_FILENAME, load_sbfl_report

from .analysis_outcomes import load_job_outcome
from .analysis_sources import AnalysisSourceResolver
from .jobs import JobManager
from .models import JobStatus


class EvidenceUnavailable(KeyError):
    pass


def _reject_constant(value):
    raise ValueError("Non-finite evidence value")


def _read(root: Path, name: str):
    path = (root / name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError('Evidence path leaves its recorded root')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=_reject_constant)


def _id(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


class EvidenceService:
    def __init__(self, jobs: JobManager, job_id: str):
        self.jobs = jobs
        self.job = jobs.get(job_id)
        if self.job.status is not JobStatus.succeeded or self.job.kind != 'localization':
            raise EvidenceUnavailable('Evidence is available for completed localization jobs')
        outcome = load_job_outcome(jobs, self.job)
        if outcome['conclusion']['status'] != 'available':
            raise EvidenceUnavailable('No localization candidates are available')
        self.resolver = AnalysisSourceResolver(jobs)
        self.root = self.resolver.result_dir(self.job)
        self.tbfv = self.resolver.upstream_record(self.job, 'tbfv_job_id', 'tbfv')
        self.csc = self.resolver.upstream_record(self.tbfv, 'csc_job_id', 'csc') if self.tbfv else None
        if not self.tbfv or not self.csc:
            raise ValueError('Evidence lineage is missing or invalid')
        tbfv_outcome = load_job_outcome(jobs, self.tbfv)
        if tbfv_outcome['conclusion']['status'] != 'fail':
            raise ValueError('Upstream verification has no reliable failure')
        self.snapshot = _read(self.root, 'cct.snapshot.json')
        validate_cct_snapshot(self.snapshot)
        self.nodes = {n['id']: n for n in self.snapshot['nodes']}
        self.edges = {e['id']: e for e in self.snapshot['edges']}
        self.parents = {e['targetNodeId']: e for e in self.edges.values() if e.get('targetNodeId')}
        self.tests = {}
        for node in self.nodes.values():
            for test in node.get('testCaseIds', []):
                self.tests[test] = node
        self.report = _read(self.resolver.result_dir(self.tbfv), 'refined_tbfv_report.json')
        counts = Counter(row['status'] for row in self.report['results'])
        categories = {'passed': 'pass', 'failed': 'fail', 'unknown': 'unknown', 'unsupported': 'unsupported', 'skipped': 'skipped'}
        if set(counts) - set(categories.values()) or any(counts[status] != tbfv_outcome['summary'][field] for field, status in categories.items()):
            raise ValueError('Verification results contradict their recorded conclusion')
        self.failures = {}
        for i, result in enumerate(self.report['results']):
            if result['status'] == 'fail':
                test = result['test_case_id']
                if test not in self.tests:
                    raise ValueError('Failed test is absent from the CCT snapshot')
                key = _id(job_id, test, result['fsf_id'])
                if key in self.failures:
                    raise ValueError('Duplicate verification evidence')
                self.failures[key] = (i, result)
        self.paths = {test: self._path(node['id']) for test, node in self.tests.items()}
        sbfl_path = (self.root / DEFAULT_SBFL_FILENAME).resolve()
        if self.root.resolve() not in sbfl_path.parents or not sbfl_path.is_file():
            raise ValueError('SBFL report leaves its recorded root')
        sbfl = load_sbfl_report(sbfl_path)
        if outcome['summary']['candidateCount'] != len(sbfl['ranking']):
            raise ValueError('SBFL ranking contradicts its recorded conclusion')
        self.report_method = sbfl['method']
        overlay = sbfl.get('cctOverlay', {})
        self.overlay = {
            'method': sbfl['method'],
            'nodes': overlay.get('nodes', {}),
            'edges': overlay.get('edges', {}),
        }
        if not set(self.overlay['nodes']) <= self.nodes.keys() or not set(self.overlay['edges']) <= self.edges.keys():
            raise ValueError('SBFL overlay references an absent CCT context')
        source_root = self.jobs.job_dir(self.job.id) / 'input'
        source_candidates = [path.resolve() for path in source_root.glob('*.java') if path.is_file()]
        if len(source_candidates) != 1:
            raise ValueError('Localization evidence requires exactly one Java source file')
        source = source_candidates[0]
        if source_root.resolve() not in source.parents:
            raise ValueError('Source file leaves the job input directory')
        self.source = source
        source_lines = source.read_text(encoding='utf-8').splitlines()
        self.candidates = []
        for i, row in enumerate(sbfl['ranking']):
            line = row['line']
            if not 1 <= line <= len(source_lines):
                raise ValueError('SBFL candidate line is outside the recorded input')
            node_ids = sorted(node_id for node_id, value in self.overlay['nodes'].items() if value.get('line') == line)
            edge_ids = sorted(edge_id for edge_id, value in self.overlay['edges'].items() if value.get('line') == line)
            failed_tests = set(row['failedTestIds'])
            support = [key for key, (_, failure) in self.failures.items() if failure['test_case_id'] in failed_tests]
            self.candidates.append({
                'id': _id(job_id, 'line', line), 'group': 'lines',
                'rank': row['rank'], 'score': row['score'], 'tieCount': row['tieGroupSize'],
                'method': sbfl['method'], 'condition': source_lines[line - 1].strip(),
                'line': line, 'statementLines': [line],
                'nodeIds': node_ids, 'edgeIds': edge_ids,
                'failureIds': support, 'recordedFailCount': row['a_ef'],
                'spectrum': {key: row[key] for key in ('a_ef', 'a_ep', 'a_nf', 'a_np')},
                'reference': {'artifact': DEFAULT_SBFL_FILENAME, 'pointer': f'/ranking/{i}'},
                'warning': None if support else 'No failing test covers this source line.',
            })
        if len({c['id'] for c in self.candidates}) != len(self.candidates):
            raise ValueError('Duplicate candidate ID')

    def _path(self, leaf):
        nodes, edges = [leaf], []
        while leaf in self.parents:
            edge = self.parents[leaf]
            edges.append(edge['id'])
            leaf = edge['sourceNodeId']
            nodes.append(leaf)
        return {'nodeIds': nodes[::-1], 'edgeIds': edges[::-1]}

    def index(self):
        return {'version': 1, 'jobId': self.job.id, 'method': self.report_method,
                'overlay': self.overlay, 'candidates': self.candidates,
                'failures': [{'id': key, 'testId': r['test_case_id'], 'fsfId': r['fsf_id']}
                             for key, (_, r) in self.failures.items()]}

    def detail(self, candidate_id: str, failure_id: str):
        candidate = next((c for c in self.candidates if c['id'] == candidate_id), None)
        if candidate is None or failure_id not in candidate['failureIds']:
            raise EvidenceUnavailable('This failure does not support the selected candidate')
        i, failure = self.failures[failure_id]
        test = failure['test_case_id']
        node = self.tests[test]
        path = self.paths[test]
        source = self.source
        lines = source.read_text(encoding='utf-8').splitlines()
        source_lines = set(candidate['statementLines'])
        if candidate['line']:
            source_lines.add(candidate['line'])
        if any(type(line) is not int or not 1 <= line <= len(lines) for line in source_lines):
            raise ValueError('Candidate source line is outside the recorded input')
        steps = []
        for edge_id in path['edgeIds']:
            edge = self.edges[edge_id]
            condition = self.nodes[edge['sourceNodeId']].get('condition', {})
            steps.append({'nodeId': edge['sourceNodeId'], 'edgeId': edge_id,
                          'line': condition.get('line'), 'expression': condition.get('expression'),
                          'inputConstraint': condition.get('inputConstraint'), 'outcome': edge['outcome']})
        csc_root = self.resolver.result_dir(self.csc)
        records = _read(csc_root, 'testcases.json')
        matches = [r for r in records if _test_case_id_from_record(r) == test]
        trace, trace_warning = [], None
        try:
            if len(matches) != 1 or not matches[0].get('trace_path'):
                raise ValueError('Test has no unique recorded trace')
            trace_path = Path(matches[0]['trace_path'])
            if not trace_path.is_absolute():
                trace_path = csc_root / trace_path
            trace_path = trace_path.resolve()
            if csc_root not in trace_path.parents:
                raise ValueError('Recorded trace is outside the upstream result directory')
            trace = [json.loads(line, parse_constant=_reject_constant) for line in trace_path.read_text().splitlines() if line.strip()]
        except (OSError, ValueError):
            trace_warning = 'The original execution trace is unavailable; the recorded CCT path is shown.'
        return {'jobId': self.job.id, 'candidateId': candidate_id, 'failureId': failure_id,
                'testId': test, 'leafNodeId': node['id'], 'inputs': node.get('inputsByTestCase', {}).get(test, {}),
                'verification': failure, 'path': steps, 'trace': trace, 'traceWarning': trace_warning,
                'source': {'filename': source.name, 'lines': lines, 'highlightLines': sorted(source_lines)},
                'references': [
                    {'jobId': self.tbfv.id, 'artifact': 'refined_tbfv_report.json', 'pointer': f'/results/{i}'},
                    {'jobId': self.job.id, **candidate['reference']},
                    {'jobId': self.job.id, 'artifact': 'cct.snapshot.json', 'nodeId': node['id']},
                ]}
