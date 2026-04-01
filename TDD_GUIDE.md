# Red-Green-Refactor (TDD) Development Guide

This document establishes the TDD methodology for the Polymarket Arbitrage Bot codebase.

## Overview

**Red-Green-Refactor** is the core Test-Driven Development cycle:

1. **RED** - Write a failing test that defines expected behavior
2. **GREEN** - Write the minimal code to make the test pass
3. **REFACTOR** - Improve the code while keeping tests green

## Test Structure

```
polym/
├── tests/
│   ├── __init__.py          # Test suite documentation
│   ├── conftest.py          # Shared fixtures and mocks
│   ├── test_arbitrage.py    # Core arbitrage logic tests
│   ├── test_portfolio.py    # Portfolio management tests
│   ├── test_rate_limiter.py # Rate limiting tests
│   └── test_security.py     # Security and validation tests
└── test_post_sale_cooldown.py  # Legacy cooldown tests
```

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_arbitrage.py -v

# Run with coverage
python -m pytest tests/ --cov=src --cov-report=html

# Run only failing tests
python -m pytest tests/ --lf

# Run tests matching pattern
python -m pytest tests/ -k "test_time_condition"
```

## Writing New Tests (TDD Workflow)

### Step 1: RED - Write a Failing Test

```python
# tests/test_new_feature.py

class TestNewFeature:
    """Tests for the new feature."""
    
    def test_feature_does_expected_thing(self):
        """Test: Feature should do X when given Y."""
        # ARRANGE - Set up test data
        input_data = "test input"
        
        # ACT - Call the function
        result = new_feature(input_data)
        
        # ASSERT - Verify the result
        assert result == "expected output"
```

### Step 2: GREEN - Make It Pass

Implement the minimal code in `src/` to make the test pass:

```python
# src/new_feature.py

def new_feature(input_data: str) -> str:
    """Minimal implementation to pass test."""
    return "expected output"
```

### Step 3: REFACTOR - Improve Code

Once tests are green, improve the implementation:

```python
# src/new_feature.py

def new_feature(input_data: str) -> str:
    """
    Process input data and return transformed result.
    
    Args:
        input_data: The input string to process
        
    Returns:
        Transformed string based on business rules
    """
    # Clean implementation with proper error handling
    if not input_data:
        raise ValueError("Input cannot be empty")
    
    return process_transformation(input_data)
```

## Test Fixtures (conftest.py)

The `conftest.py` provides reusable mocks:

### Mock Oracle
```python
def test_with_oracle(mock_oracle):
    # Configure mock prices
    mock_oracle.set_price("BTC", 50000.0)
    mock_oracle.set_rolling_average("BTC", 49900.0)
    mock_oracle.set_stale("BTC", False)
    
    # Use in tests
    price = mock_oracle.get_price("BTC")
```

### Mock Sniper (Order Book)
```python
def test_with_sniper(mock_sniper):
    # Configure mock order book
    mock_sniper.set_best_ask("token-yes", 0.95)
    mock_sniper.set_best_bid("token-yes", 0.94)
    
    # Use in tests
    ask = mock_sniper.get_best_ask("token-yes")
```

### Mock Executor
```python
def test_with_executor(mock_executor):
    # Configure mock execution
    mock_executor.set_balance(100.0)
    mock_executor.set_execution_success(True)
    
    # Use in tests
    result = await mock_executor.execute_fok_order(...)
```

### Market Builder
```python
def test_with_market():
    market = (MarketBuilder()
        .with_asset("BTC")
        .with_question("Will BTC go up?")
        .with_tokens("yes-token", "no-token")
        .expiring_in_seconds(0.5)
        .build())
```

## Test Categories

### Unit Tests
Test individual functions/methods in isolation:

```python
class TestArbitrageEngineConditions:
    """Test individual conditions separately."""
    
    def test_time_condition_met(self):
        # Test ONLY the time condition
        pass
    
    def test_oracle_alignment(self):
        # Test ONLY the oracle alignment
        pass
```

### Integration Tests
Test multiple components working together:

```python
class TestArbitrageEngineFullAnalysis:
    """Test full analysis with all conditions."""
    
    async def test_all_conditions_met(self):
        # Test the complete flow
        pass
```

### Edge Case Tests
Test boundary conditions and error paths:

```python
class TestEdgeCases:
    def test_expired_market_handling(self):
        pass
    
    def test_stale_oracle_data(self):
        pass
    
    def test_missing_order_book(self):
        pass
```

## Best Practices

### 1. One Assertion Per Concept
```python
# Good - focused test
def test_position_size_for_dry_run(self):
    assert engine.calculate_position_size() == 5.0

# Bad - multiple unrelated assertions
def test_everything(self):
    assert size == 5.0
    assert mode == TradingMode.DRY_RUN
    assert balance > 0
```

### 2. Descriptive Test Names
```python
# Good - describes behavior
def test_time_condition_not_met_for_expired_market(self):
    pass

# Bad - vague
def test_time(self):
    pass
```

### 3. Arrange-Act-Assert Pattern
```python
def test_example(self):
    # ARRANGE - Set up test data
    market = MarketBuilder().build()
    
    # ACT - Execute the code
    result = engine.analyze(market)
    
    # ASSERT - Verify the outcome
    assert result is not None
```

### 4. Use Fixtures for Common Setup
```python
# conftest.py
@pytest.fixture
def configured_engine(mock_oracle, mock_sniper):
    return ArbitrageEngine(oracle=mock_oracle, sniper=mock_sniper)

# test file
def test_with_fixture(configured_engine):
    result = configured_engine.analyze(market)
```

### 5. Test Error Cases
```python
def test_invalid_input_raises_error(self):
    with pytest.raises(ValueError):
        engine.process(invalid_input)
```

## Coverage Goals

| Module | Current | Target |
|--------|---------|--------|
| arbitrage.py | 0% | 80% |
| portfolio.py | 0% | 80% |
| rate_limiter.py | 0% | 70% |
| security.py | 0% | 70% |
| executor.py | 0% | 60% |
| oracle.py | 0% | 60% |
| scanner.py | 0% | 50% |

## Adding Tests for New Features

When adding a new feature:

1. **Create test file** (or add to existing): `tests/test_feature.py`
2. **Write failing tests** describing expected behavior
3. **Implement feature** in `src/`
4. **Run tests** to verify green state
5. **Refactor** if needed while keeping tests green
6. **Update this guide** if new patterns emerge

## Continuous Integration

Tests should run on every commit:

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: python -m pytest tests/ -v --cov=src
```
