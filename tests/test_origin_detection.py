
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
from typing import Any, Dict, Optional

# Import the module under test
from src.processing.origin_detection.types import CandidateMR, OriginDetectionConfig, EndpointChange, ChangeType
from src.processing.origin_detection.candidate_collector import TargetBranchCollector, CommitMessageCollector
from src.processing.origin_detection.endpoint_detector import EndpointChangeDetector
from src.processing.origin_detection.resolver import OriginMRResolver

class TestTargetBranchCollector(unittest.IsolatedAsyncioTestCase):
    async def test_collect_candidates(self):
        # Setup
        client = MagicMock()
        client.get_merged_mrs = AsyncMock(return_value=[
            {'iid': 101, 'title': 'Feature A', 'source_branch': 'feat/a', 'target_branch': 'release/v1'},
            {'iid': 102, 'title': 'Feature B', 'source_branch': 'feat/b', 'target_branch': 'release/v1'}
        ])
        
        parent_mr = {'iid': 999, 'source_branch': 'release/v1'}
        config = OriginDetectionConfig()
        collector = TargetBranchCollector()
        
        # Act
        candidates = await collector.collect(client, "group/project", parent_mr, config)
        
        # Assert
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].iid, 101)
        self.assertEqual(candidates[1].iid, 102)
        
    async def test_skips_parent_mr(self):
        # Setup - returns parent MR in list
        client = MagicMock()
        client.get_merged_mrs = AsyncMock(return_value=[
            {'iid': 999, 'title': 'Release v1', 'source_branch': 'release/v1', 'target_branch': 'master'},
            {'iid': 101, 'title': 'Feature A', 'source_branch': 'feat/a', 'target_branch': 'release/v1'}
        ])
        
        parent_mr = {'iid': 999, 'source_branch': 'release/v1'}
        config = OriginDetectionConfig()
        collector = TargetBranchCollector()
        
        # Act
        candidates = await collector.collect(client, "group/project", parent_mr, config)
        
        # Assert
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].iid, 101)

class TestCommitMessageCollector(unittest.IsolatedAsyncioTestCase):
    async def test_collect_from_merge_commits(self):
        # Setup
        client = MagicMock()
        # Mock commits list
        client.get_mr_commits = AsyncMock(return_value=[
            {'title': "Merge branch 'feature/LOG-123'"},
            {'title': "Update README"}
        ])
        # Mock MR search
        client.search_mrs_by_source_branch = AsyncMock(return_value=[
            {'iid': 201, 'title': 'Feature 123', 'source_branch': 'feature/LOG-123'}
        ])
        
        parent_mr = {'iid': 888, 'source_branch': 'release/v2'}
        config = OriginDetectionConfig()
        collector = CommitMessageCollector()
        
        # Act
        candidates = await collector.collect(client, "group/project", parent_mr, config)
        
        # Assert
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].iid, 201)
        self.assertEqual(candidates[0].source_branch, 'feature/LOG-123')

class TestEndpointChangeDetector(unittest.IsolatedAsyncioTestCase):
    def test_split_component_file_touches_configured_source(self):
        detector = EndpointChangeDetector(AsyncMock(), OriginDetectionConfig())

        self.assertTrue(
            detector._touches_swagger(
                ['docs/common/responses.yaml'],
                'docs',
            )
        )

    async def test_detect_change_modified(self):
        # Setup
        client = MagicMock()
        client.get_mr_changed_files = AsyncMock(return_value=['api/swagger/openapi.yaml'])
        client.get_mr_diff_shas = AsyncMock(return_value=('sha_base', 'sha_head'))
        
        # Mock spec download
        mock_download = AsyncMock()
        mock_download.side_effect = [
            {'paths': {'/test': {'get': {'summary': 'Old'}}}}, # Base spec
            {'paths': {'/test': {'get': {'summary': 'New', 'parameters': []}}}}  # Head spec
        ]
        
        config = OriginDetectionConfig()
        detector = EndpointChangeDetector(mock_download, config)
        candidate = CandidateMR(iid=300, title="T", source_branch="b", target_branch="t")
        
        # Mock SchemaExtractor (since we rely on it)
        with patch('src.processing.origin_detection.endpoint_detector.SchemaExtractor') as MockExtractor:
            MockExtractor.extract.side_effect = [
                {'summary': 'Old'}, # Before
                {'summary': 'New', 'parameters': []}  # After
            ]
            
            # Act
            change = await detector.detect(
                client, "p", candidate, "api/swagger", 
                "GET /test", "get", "/test"
            )
            
            # Assert
            self.assertIsNotNone(change)
            self.assertEqual(change.change_type, 'modified')
            self.assertEqual(change.mr.iid, 300)

class TestOriginMRResolver(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_finds_deep_change(self):
        # Setup
        client = MagicMock()
        parent_mr = {'iid': 1000, 'source_branch': 'release/v3'}
        
        # Mocks
        mock_collector = MagicMock()
        candidate = CandidateMR(iid=500, title="F", source_branch="f", target_branch="r")
        mock_collector.collect = AsyncMock(return_value=[candidate])
        
        mock_detector = MagicMock()
        # Detects change in candidate 500
        mock_detector.detect = AsyncMock(return_value=EndpointChange("k", "modified", candidate))
        
        config = OriginDetectionConfig()
        resolver = OriginMRResolver(
            collectors=[mock_collector],
            detector=mock_detector,
            config=config
        )
        
        # Act
        result = await resolver.resolve(
            client, "p", parent_mr, "api/swagger", 
            "GET /foo", "get", "/foo"
        )
        
        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(result['iid'], 500)
        
if __name__ == '__main__':
    unittest.main()
