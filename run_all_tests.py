#!/usr/bin/env python
"""
Run all LCM tests and generate summary report.

Usage: python run_all_tests.py
"""

import subprocess
import sys


def run_tests():
    """Run pytest and capture results."""
    print("=" * 70)
    print("LCM Protocol - Full Test Suite")
    print("=" * 70)
    print()
    
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
        capture_output=False
    )
    
    return result.returncode


if __name__ == "__main__":
    exit_code = run_tests()
    
    if exit_code == 0:
        print("\n" + "=" * 70)
        print("✓ All tests passed successfully!")
        print("=" * 70)
    else:
        print("\n" + "=" * 70)
        print("✗ Some tests failed")
        print("=" * 70)
        sys.exit(exit_code)
