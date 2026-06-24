#!/usr/bin/env python3
"""
Diagnostic script to check Claude OCR setup.
Run this to verify everything is configured correctly.
"""
import os
import sys

def check_api_key():
    """Check if ANTHROPIC_API_KEY is set."""
    print("\n📋 Checking ANTHROPIC_API_KEY...")
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set!")
        print("   Fix: export ANTHROPIC_API_KEY=sk-your-actual-key")
        return False

    if not api_key.startswith("sk-"):
        print("⚠️  ANTHROPIC_API_KEY doesn't start with 'sk-'")
        print(f"   Value: {api_key[:20]}...")
        return False

    print(f"✅ ANTHROPIC_API_KEY is set")
    print(f"   Value: {api_key[:20]}...{api_key[-5:]}")
    return True


def check_imports():
    """Check if required packages can be imported."""
    print("\n📦 Checking imports...")

    required = {
        "anthropic": "Claude API client",
        "google.cloud.bigquery": "BigQuery client",
        "pandas": "Excel parsing",
    }

    all_ok = True
    for module, description in required.items():
        try:
            __import__(module)
            print(f"✅ {module:30} ({description})")
        except ImportError as e:
            print(f"❌ {module:30} - {e}")
            all_ok = False

    return all_ok


def check_claude_connection():
    """Test actual connection to Claude API."""
    print("\n🤖 Testing Claude API connection...")

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("⚠️  Skipping - ANTHROPIC_API_KEY not set")
        return False

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        # Try a simple API call
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=100,
            messages=[{"role": "user", "content": "Say 'ok'"}]
        )

        print(f"✅ Claude API connection successful")
        print(f"   Response: {response.content[0].text[:50]}")
        return True
    except Exception as e:
        print(f"❌ Claude API connection failed: {e}")
        return False


def check_ocr_functions():
    """Check if OCR functions can be imported."""
    print("\n🔍 Checking OCR functions...")

    try:
        from ocr_simulator import simulate_ocr, _has_valid_data
        print(f"✅ ocr_simulator.py imports successfully")

        from claude_ocr_enhance import enhance_with_claude, claude_ocr_fallback
        print(f"✅ claude_ocr_enhance.py imports successfully")

        return True
    except Exception as e:
        print(f"❌ OCR import failed: {e}")
        return False


def test_ocr_with_sample():
    """Test OCR with sample data."""
    print("\n🧪 Testing OCR with sample data...")

    # Simple CSV with insufficient data (should trigger Claude)
    sample_csv = b"""company,revenue,assets
Unknown,0,0
Company A,,1000
"""

    try:
        from ocr_simulator import simulate_ocr, _has_valid_data

        print("   Testing _has_valid_data()...")
        records = [
            {"company_name": "Unknown", "revenue_usd_m": 0, "ebitda_usd_m": 0},
            {"company_name": "Shell", "revenue_usd_m": 400000, "ebitda_usd_m": 100000},
        ]

        for i, rec in enumerate(records):
            is_valid = _has_valid_data([rec])
            status = "✅ Valid" if is_valid else "❌ Invalid (would trigger Claude)"
            print(f"      Record {i}: {rec.get('company_name'):20} - {status}")

        print("   Testing simulate_ocr()...")
        result = simulate_ocr(sample_csv, "test.csv")
        print(f"      Result: {len(result)} records")
        if not result:
            print(f"      ✅ Empty as expected (would trigger Claude)")

        return True
    except Exception as e:
        print(f"❌ OCR test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_claude_fallback():
    """Test Claude fallback function."""
    print("\n🤖 Testing Claude fallback...")

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("⚠️  Skipping - ANTHROPIC_API_KEY not set")
        return False

    try:
        from claude_ocr_enhance import claude_ocr_fallback

        # Test with simple text that looks like financial data
        sample_text = b"""
        ACME Corp
        Country: USA
        Sector: Trading
        Revenue: $500M
        EBITDA: $150M
        Assets: $2000M
        Debt: $600M
        """

        print("   Testing claude_ocr_fallback with sample PDF content...")
        result = claude_ocr_fallback(sample_text, "test.txt", [])

        print(f"   Result keys: {list(result.keys())}")
        print(f"   Records extracted: {len(result.get('records', []))}")
        print(f"   Confidence: {result.get('confidence')}")

        if result.get("error"):
            print(f"   Error: {result.get('error')}")
            return False

        if result.get('records'):
            print(f"   ✅ Claude successfully extracted records")
            print(f"      Company: {result['records'][0].get('company_name')}")
        else:
            print(f"   ⚠️  No records extracted (might need better file content)")

        return True
    except Exception as e:
        print(f"❌ Claude fallback test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all diagnostics."""
    print("=" * 70)
    print("  Claude OCR Diagnostic Tool")
    print("=" * 70)

    results = {
        "API Key": check_api_key(),
        "Imports": check_imports(),
        "Claude Connection": check_claude_connection(),
        "OCR Functions": check_ocr_functions(),
        "OCR Validation": test_ocr_with_sample(),
        "Claude Fallback": test_claude_fallback(),
    }

    print("\n" + "=" * 70)
    print("  Summary")
    print("=" * 70)

    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:25} {status}")

    all_passed = all(results.values())

    if all_passed:
        print("\n🎉 All checks passed! Your setup is ready.")
    else:
        print("\n⚠️  Some checks failed. See details above.")
        print("\nCommon fixes:")
        print("  1. Set ANTHROPIC_API_KEY: export ANTHROPIC_API_KEY=sk-...")
        print("  2. Install packages: pip install anthropic google-cloud-bigquery pandas")
        print("  3. Check network/firewall: should allow api.anthropic.com")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
