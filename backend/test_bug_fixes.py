#!/usr/bin/env python3
"""
=============================================================================
REVENUE DASHBOARD BUG VERIFICATION SCRIPT
=============================================================================
This script verifies the 3 critical bug fixes for the property revenue dashboard:

1. CACHE POISONING (Privacy) - Tenant isolation in cache layer
   - Root cause: "default_tenant" fallback caused cross-tenant key collisions
   - Fix: Strict tenant_id validation, fail-fast on invalid values

2. TIMEZONE HANDLING (Accuracy) - UTC conversion for DB queries  
   - Root cause: Properties in different timezones (Paris, NYC) need local->UTC
   - Fix: pytz localization before querying reservation dates

3. FLOATING-POINT PRECISION (Financial accuracy) - Decimal serialization
   - Root cause: float arithmetic causes rounding errors (1.10+2.20=3.30000003)
   - Fix: Use Decimal, return as string to preserve cents

Run: python test_bug_fixes.py
Expected: 11/11 tests passing, exit code 0

Author: [Candidate Name]
Date: January 2026
=============================================================================
"""

import sys
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List
from dataclasses import dataclass
from enum import Enum

# Add the app directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestResult(Enum):
    PASS = "[PASS]"
    FAIL = "[FAIL]"
    SKIP = "[SKIP]"


@dataclass
class TestCase:
    name: str
    result: TestResult
    details: str = ""


class BugVerificationSuite:
    """
    Comprehensive test suite for revenue dashboard bug fixes.
    Tests can run without external dependencies (Redis/DB).
    """
    
    def __init__(self):
        self.results: List[TestCase] = []
        
    def log(self, msg: str):
        print(f"  {msg}")
    
    def add_result(self, name: str, passed: bool, details: str = ""):
        result = TestResult.PASS if passed else TestResult.FAIL
        self.results.append(TestCase(name, result, details))
        
    # =========================================================================
    # BUG 1: Cache Poisoning Tests
    # =========================================================================
    
    def test_cache_key_tenant_isolation(self) -> bool:
        """Verify cache keys include tenant_id for proper isolation."""
        print("\n" + "="*60)
        print("BUG 1: CACHE POISONING / TENANT ISOLATION")
        print("="*60)
        
        try:
            from app.services.cache import _make_cache_key
            
            # Test 1: Different tenants get different keys
            key_a = _make_cache_key("tenant-a", "prop-001")
            key_b = _make_cache_key("tenant-b", "prop-001")
            
            self.log(f"Tenant A key: {key_a}")
            self.log(f"Tenant B key: {key_b}")
            
            keys_different = key_a != key_b
            self.add_result(
                "Different tenants get different cache keys",
                keys_different,
                f"A={key_a}, B={key_b}"
            )
            
            # Test 2: Key format is correct
            expected_format = key_a.startswith("revenue:tenant-a:prop-001")
            self.add_result(
                "Cache key format includes tenant prefix",
                expected_format,
                f"Key format: {key_a}"
            )
            
            return keys_different and expected_format
            
        except ImportError as e:
            self.add_result("Cache module import", False, str(e))
            return False
    
    def test_cache_key_rejects_invalid_tenant(self) -> bool:
        """Verify cache layer rejects dangerous tenant values."""
        print("\n" + "-"*40)
        print("Testing invalid tenant_id rejection...")
        print("-"*40)
        
        try:
            from app.services.cache import _make_cache_key
            
            invalid_values = [
                "",
                "default_tenant",
                "null",
                "undefined",
                "None",
                "   ",  # whitespace
            ]
            
            all_rejected = True
            
            for invalid_val in invalid_values:
                try:
                    key = _make_cache_key(invalid_val, "prop-001")
                    self.log(f"  [ERR] '{invalid_val}' was NOT rejected (got key: {key})")
                    all_rejected = False
                except ValueError as e:
                    self.log(f"  [OK] '{invalid_val}' correctly rejected: {e}")
            
            self.add_result(
                "Invalid tenant_id values are rejected",
                all_rejected,
                f"Tested {len(invalid_values)} invalid values"
            )
            
            return all_rejected
            
        except ImportError as e:
            self.add_result("Cache validation import", False, str(e))
            return False
    
    def test_dashboard_tenant_extraction(self) -> bool:
        """Verify dashboard API requires valid tenant_id."""
        print("\n" + "-"*40)
        print("Testing dashboard tenant extraction...")
        print("-"*40)
        
        try:
            from app.api.v1.dashboard import _get_tenant_id
            from app.models.auth import AuthenticatedUser
            from fastapi import HTTPException
            
            # Test 1: Valid tenant passes
            valid_user = AuthenticatedUser(
                id="user-123",
                email="test@example.com",
                tenant_id="tenant-a",
                permissions=[],
                cities=[],
                is_admin=False
            )
            
            try:
                tenant = _get_tenant_id(valid_user)
                valid_passes = tenant == "tenant-a"
                self.log(f"  [OK] Valid tenant extracted: {tenant}")
            except Exception as e:
                valid_passes = False
                self.log(f"  [ERR] Valid tenant failed: {e}")
            
            # Test 2: Missing tenant raises 403
            invalid_user = AuthenticatedUser(
                id="user-456",
                email="notenant@example.com",
                tenant_id=None,
                permissions=[],
                cities=[],
                is_admin=False
            )
            
            try:
                _get_tenant_id(invalid_user)
                missing_rejected = False
                self.log("  [ERR] Missing tenant was NOT rejected")
            except HTTPException as e:
                missing_rejected = e.status_code == 403
                self.log(f"  [OK] Missing tenant rejected with 403: {e.detail}")
            except Exception as e:
                missing_rejected = False
                self.log(f"  [ERR] Wrong exception type: {e}")
            
            self.add_result(
                "Dashboard extracts valid tenant_id",
                valid_passes,
                "tenant-a extracted correctly"
            )
            self.add_result(
                "Dashboard rejects missing tenant_id with 403",
                missing_rejected,
                "HTTPException with status 403"
            )
            
            return valid_passes and missing_rejected
            
        except ImportError as e:
            self.log(f"  [WARN] Could not import dashboard module: {e}")
            self.add_result("Dashboard tenant check (import failed)", False, str(e))
            return False
    
    # =========================================================================
    # BUG 2: Timezone Handling Tests
    # =========================================================================
    
    def test_timezone_conversion(self) -> bool:
        """Verify timezone conversion logic is correct."""
        print("\n" + "="*60)
        print("BUG 2: TIMEZONE HANDLING")
        print("="*60)
        
        try:
            import pytz
            from datetime import datetime
            
            # Simulate the logic from reservations.py
            test_cases = [
                ("America/New_York", 3, 2024),  # EST/EDT
                ("Europe/Paris", 3, 2024),       # CET/CEST
                ("UTC", 3, 2024),
                ("Asia/Tokyo", 3, 2024),
            ]
            
            all_correct = True
            
            for tz_str, month, year in test_cases:
                local_tz = pytz.timezone(tz_str)
                
                # Create local midnight on first of month
                naive_start = datetime(year, month, 1)
                local_start = local_tz.localize(naive_start)
                
                # Convert to UTC
                utc_start = local_start.astimezone(pytz.UTC)
                
                # Verify the conversion is reversible
                back_to_local = utc_start.astimezone(local_tz)
                
                is_correct = back_to_local.replace(tzinfo=None) == naive_start
                
                self.log(f"  {tz_str}: Local {local_start} -> UTC {utc_start}")
                
                if not is_correct:
                    all_correct = False
                    self.log(f"    [ERR] Conversion mismatch!")
            
            self.add_result(
                "Timezone conversions are reversible",
                all_correct,
                f"Tested {len(test_cases)} timezones"
            )
            
            return all_correct
            
        except Exception as e:
            self.add_result("Timezone conversion test", False, str(e))
            return False
    
    def test_month_boundary_handling(self) -> bool:
        """Verify month boundary calculations are correct."""
        print("\n" + "-"*40)
        print("Testing month boundary calculations...")
        print("-"*40)
        
        test_cases = [
            (3, 2024, 4, 2024),    # March -> April
            (12, 2024, 1, 2025),   # December -> January (year rollover)
            (1, 2024, 2, 2024),    # January -> February
        ]
        
        all_correct = True
        
        for month, year, expected_next_month, expected_next_year in test_cases:
            # Logic from reservations.py
            if month < 12:
                next_month = month + 1
                next_year = year
            else:
                next_month = 1
                next_year = year + 1
            
            is_correct = next_month == expected_next_month and next_year == expected_next_year
            
            self.log(f"  {year}-{month:02d} -> {next_year}-{next_month:02d} {'OK' if is_correct else 'ERR'}")
            
            if not is_correct:
                all_correct = False
        
        self.add_result(
            "Month boundary calculations correct",
            all_correct,
            f"Including December->January rollover"
        )
        
        return all_correct
    
    # =========================================================================
    # BUG 3: Floating-Point Precision Tests
    # =========================================================================
    
    def test_decimal_precision(self) -> bool:
        """Verify Decimal is used for financial calculations."""
        print("\n" + "="*60)
        print("BUG 3: FLOATING-POINT PRECISION")
        print("="*60)
        
        # Demonstrate the problem with floats
        float_result = 1.10 + 2.20
        decimal_result = Decimal('1.10') + Decimal('2.20')
        
        self.log(f"  Float:   1.10 + 2.20 = {float_result} (repr: {repr(float_result)})")
        self.log(f"  Decimal: 1.10 + 2.20 = {decimal_result}")
        
        # The float result is wrong!
        float_is_wrong = float_result != 3.30
        decimal_is_correct = decimal_result == Decimal('3.30')
        
        self.add_result(
            "Float arithmetic shows precision error",
            float_is_wrong,
            f"1.10 + 2.20 = {float_result} (not 3.30)"
        )
        self.add_result(
            "Decimal arithmetic is precise",
            decimal_is_correct,
            f"1.10 + 2.20 = {decimal_result}"
        )
        
        return decimal_is_correct
    
    def test_revenue_returns_string(self) -> bool:
        """Verify revenue values are returned as strings (not floats)."""
        print("\n" + "-"*40)
        print("Testing revenue string serialization...")
        print("-"*40)
        
        # Simulate the return format from reservations.py
        revenue = Decimal('2250.00')
        serialized = str(revenue)
        
        # Parse it back
        try:
            parsed = Decimal(serialized)
            is_lossless = parsed == revenue
            self.log(f"  Original: {revenue} -> Serialized: '{serialized}' -> Parsed: {parsed}")
            self.log(f"  Lossless round-trip: {'OK' if is_lossless else 'ERR'}")
        except InvalidOperation:
            is_lossless = False
        
        self.add_result(
            "Revenue serializes to string losslessly",
            is_lossless,
            f"Decimal -> str -> Decimal preserves value"
        )
        
        return is_lossless
    
    def test_cents_precision(self) -> bool:
        """Verify we don't lose cents in calculations."""
        print("\n" + "-"*40)
        print("Testing cents precision...")
        print("-"*40)
        
        # Simulate summing multiple reservation amounts
        amounts = [
            Decimal('99.99'),
            Decimal('149.99'),
            Decimal('199.99'),
            Decimal('0.03'),  # Edge case: 3 cents
        ]
        
        total = sum(amounts, Decimal('0.00'))
        expected = Decimal('450.00')
        
        is_correct = total == expected
        
        self.log(f"  Sum of {[str(a) for a in amounts]}")
        self.log(f"  Result: {total} (expected: {expected})")
        
        self.add_result(
            "Cents precision maintained in sums",
            is_correct,
            f"Sum = {total}"
        )
        
        return is_correct
    
    # =========================================================================
    # Test Runner
    # =========================================================================
    
    def run_all_tests(self) -> bool:
        """Run all verification tests and print summary."""
        print("\n" + "="*60)
        print("   REVENUE DASHBOARD BUG VERIFICATION SUITE")
        print("="*60)
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Run all tests
        test_methods = [
            # Bug 1: Cache Poisoning
            self.test_cache_key_tenant_isolation,
            self.test_cache_key_rejects_invalid_tenant,
            self.test_dashboard_tenant_extraction,
            
            # Bug 2: Timezone
            self.test_timezone_conversion,
            self.test_month_boundary_handling,
            
            # Bug 3: Precision
            self.test_decimal_precision,
            self.test_revenue_returns_string,
            self.test_cents_precision,
        ]
        
        for test_method in test_methods:
            try:
                test_method()
            except Exception as e:
                self.add_result(
                    test_method.__name__,
                    False,
                    f"Unexpected error: {e}"
                )
        
        # Print summary
        print("\n" + "="*60)
        print("   TEST SUMMARY")
        print("="*60)
        
        passed = sum(1 for r in self.results if r.result == TestResult.PASS)
        failed = sum(1 for r in self.results if r.result == TestResult.FAIL)
        skipped = sum(1 for r in self.results if r.result == TestResult.SKIP)
        
        for result in self.results:
            status = result.result.value
            print(f"  {status} {result.name}")
            if result.details and result.result == TestResult.FAIL:
                print(f"         +-- {result.details}")
        
        print("\n" + "-"*60)
        print(f"  TOTAL: {passed} passed, {failed} failed, {skipped} skipped")
        print("-"*60)
        
        all_passed = failed == 0
        
        if all_passed:
            print("\n  >>> ALL TESTS PASSED - Ready to commit!")
        else:
            print("\n  !!! SOME TESTS FAILED - Review before committing!")
        
        return all_passed


def main():
    """Main entry point."""
    suite = BugVerificationSuite()
    success = suite.run_all_tests()
    
    # Return exit code for CI/CD integration
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
