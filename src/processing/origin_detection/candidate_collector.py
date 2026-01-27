"""
Candidate Collectors for Origin MR Detection.

This module defines the Protocol for collecting candidate MRs and provides
two implementations:
1. TargetBranchCollector - finds MRs by target branch
2. CommitMessageCollector - parses merge commit messages

Uses Python Protocol for structural subtyping (duck typing with type hints).
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Protocol, runtime_checkable

from .types import CandidateMR, OriginDetectionConfig

if TYPE_CHECKING:
    from src.gitlab.client import AsyncGitLabClient

logger = logging.getLogger(__name__)


@runtime_checkable
class CandidateCollector(Protocol):
    """
    Protocol for candidate MR collectors.
    
    Implementations must provide an async collect() method that returns
    a list of candidate MRs that might be the origin of a change.
    
    Using Protocol enables structural subtyping - any class with a matching
    collect() method signature is considered compatible.
    """
    
    async def collect(
        self,
        client: AsyncGitLabClient,
        project_path: str,
        parent_mr: Dict[str, Any],
        config: OriginDetectionConfig,
    ) -> List[CandidateMR]:
        """
        Collect candidate MRs that might be the origin of a change.
        
        Args:
            client: GitLab API client
            project_path: GitLab project path (e.g., "group/project")
            parent_mr: The parent/release MR data from GitLab API
            config: Detection configuration
            
        Returns:
            List of candidate MRs, may be empty
        """
        ...


class TargetBranchCollector:
    """
    Collects candidates by finding MRs that targeted the parent MR's source branch.
    
    This handles the standard GitLab flow:
    Feature Branch -> Release Branch -> Master
    
    When we see a merge to master from 'release-2024-08-01', we look for
    all MRs that targeted 'release-2024-08-01' as their target branch.
    """
    
    async def collect(
        self,
        client: AsyncGitLabClient,
        project_path: str,
        parent_mr: Dict[str, Any],
        config: OriginDetectionConfig,
    ) -> List[CandidateMR]:
        """Collect MRs that targeted the parent's source branch."""
        source_branch = parent_mr.get('source_branch', '')
        parent_iid = parent_mr.get('iid', 0)
        
        if not source_branch or source_branch == 'master':
            return []
        
        candidates: List[CandidateMR] = []
        seen_iids: set = set()
        
        # Search both merged and closed states
        for state in ['merged', 'closed']:
            try:
                mrs = await client.get_merged_mrs(
                    project_path,
                    target_branch=source_branch,
                    state=state,
                    limit=config.max_candidates,
                )
                
                for mr_data in mrs:
                    iid = mr_data.get('iid')
                    if iid == parent_iid:
                        continue  # Skip the parent itself
                    if iid in seen_iids:
                        continue  # Skip duplicates
                        
                    seen_iids.add(iid)
                    candidates.append(CandidateMR.from_gitlab_response(mr_data))
                    
            except Exception as e:
                logger.warning(
                    f"[TargetBranchCollector] Failed to search MRs "
                    f"for branch '{source_branch}': {e}"
                )
        
        logger.debug(
            f"[TargetBranchCollector] Found {len(candidates)} candidates "
            f"for parent !{parent_iid}"
        )
        return candidates


class CommitMessageCollector:
    """
    Collects candidates by parsing merge commit messages.
    
    This handles the manual/local merge flow where developers merge
    feature branches locally before pushing:
    
    git checkout release-2024-08-01
    git merge feature/LOGRETAIL-368
    git push
    
    The commit message typically reads: "Merge branch 'feature/LOGRETAIL-368'"
    We extract the branch name and find the corresponding MR.
    """
    
    async def collect(
        self,
        client: AsyncGitLabClient,
        project_path: str,
        parent_mr: Dict[str, Any],
        config: OriginDetectionConfig,
    ) -> List[CandidateMR]:
        """Collect MRs by parsing merge commit messages."""
        parent_iid = parent_mr.get('iid', 0)
        source_branch = parent_mr.get('source_branch', '')
        
        candidates: List[CandidateMR] = []
        seen_branches: set = set()
        
        try:
            # Fetch commits for the parent MR
            commits = await client.get_mr_commits(
                project_path, 
                parent_iid,
                limit=config.max_commits_scan
            )
            
            for commit in commits:
                title = commit.get('title', '')
                branch = config.extract_branch_from_commit(title)
                
                if not branch:
                    continue
                if branch in ['master', source_branch]:
                    continue
                if branch in seen_branches:
                    continue
                    
                seen_branches.add(branch)
                
                # Find MR for this branch
                try:
                    mrs = await client.search_mrs_by_source_branch(
                        project_path, 
                        branch,
                        limit=1
                    )
                    if mrs:
                        candidates.append(
                            CandidateMR.from_gitlab_response(mrs[0])
                        )
                except Exception as e:
                    logger.debug(f"Failed to find MR for branch '{branch}': {e}")
                    
        except Exception as e:
            logger.warning(
                f"[CommitMessageCollector] Failed to scan commits "
                f"for !{parent_iid}: {e}"
            )
        
        logger.debug(
            f"[CommitMessageCollector] Found {len(candidates)} candidates "
            f"from {len(seen_branches)} merge commits"
        )
        return candidates


def get_default_collectors() -> List[CandidateCollector]:
    """
    Factory function returning the default set of collectors.
    
    Returns collectors in priority order - TargetBranchCollector first
    as it's more reliable, CommitMessageCollector as fallback.
    """
    return [
        TargetBranchCollector(),
        CommitMessageCollector(),
    ]
