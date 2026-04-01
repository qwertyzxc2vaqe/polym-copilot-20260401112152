"""
Test suite for Polymarket Arbitrage Bot.

Following TDD (Red-Green-Refactor) methodology:
1. RED: Write a failing test first
2. GREEN: Write minimal code to make the test pass  
3. REFACTOR: Improve code while keeping tests green

Test organization:
- conftest.py: Shared fixtures and mocks
- test_arbitrage.py: Core arbitrage logic tests
- test_portfolio.py: Portfolio management tests
- test_rate_limiter.py: Rate limiting tests
- test_security.py: Security and validation tests
"""
