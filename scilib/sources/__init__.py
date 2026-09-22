"""Adapters for open bibliographic and full-text APIs.

Every source here is a documented public API used the way its operator intends.
None of them scrapes a publisher's article page: that breaks terms of use, is
actively blocked, and is the behaviour that gets an institution's whole IP range
cut off. Where a paper is not legally reachable the honest answer is to say so
and offer the author-request route, which is what `resolve.py` does.
"""
