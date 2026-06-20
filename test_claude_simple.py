#!/usr/bin/env python3
"""
Simple test to verify Claude works for OCR.
"""
import os
import sys

print("=" * 70)
print("  Claude OCR Simple Test")
print("=" * 70)

# Check API key
print("\n1. Checking ANTHROPIC_API_KEY...")
api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
if not api_key:
    print("❌ ANTHROPIC_API_KEY not set!")
    print("   Fix: export ANTHROPIC_API_KEY=sk-ant-v1-your-key")
    sys.exit(1)
print(f"✅ API key found: {api_key[:20]}...")

# Import Claude
print("\n2. Importing Anthropic...")
try:
    from anthropic import Anthropic
    print("✅ Anthropic imported")
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

# Create client
print("\n3. Creating Claude client...")
try:
    client = Anthropic(api_key=api_key)
    print("✅ Client created")
except Exception as e:
    print(f"❌ Failed: {e}")
    sys.exit(1)

# Test extraction
print("\n4. Testing financial data extraction...")
test_text = """
SHELL GLOBAL LTD
Headquarters: Netherlands
Sector: Oil & Gas
Period: 2024

Financial Summary:
Revenue: $400,000 million
EBITDA: $120,000 million
Net Income: $80,000 million
Total Assets: $2,500,000 million
Total Debt: $800,000 million
Current Ratio: 1.5x
Credit Rating: BBB
"""

prompt = f"""Extract financial information and return ONLY a JSON array.
Each record needs: company_name, country, sector, credit_rating, period_year, revenue_usd_m, ebitda_usd_m, net_income_usd_m, total_assets_usd_m, total_debt_usd_m, current_ratio.
Use null or 0 for missing values.
Return ONLY JSON array, nothing else.

Example: [{{"company_name": "Shell", "country": "Netherlands", "sector": "Oil & Gas", "credit_rating": "BBB", "period_year": 2024, "revenue_usd_m": 400000, "ebitda_usd_m": 120000, "net_income_usd_m": 80000, "total_assets_usd_m": 2500000, "total_debt_usd_m": 800000, "current_ratio": 1.5}}]

Document:
{test_text}
"""

try:
    print("   Calling Claude...")
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    response_text = response.content[0].text.strip()
    print(f"✅ Claude responded")
    print(f"\n   Raw response:\n   {response_text[:200]}...")

    # Try to parse JSON
    import json
    import re

    try:
        records = json.loads(response_text)
    except:
        match = re.search(r'\[.*\]', response_text, re.DOTALL)
        if match:
            records = json.loads(match.group())
        else:
            records = []

    if records:
        print(f"\n✅ Extracted {len(records)} record(s)")
        for i, rec in enumerate(records):
            print(f"\n   Record {i+1}:")
            print(f"      Company: {rec.get('company_name')}")
            print(f"      Country: {rec.get('country')}")
            print(f"      Revenue: ${rec.get('revenue_usd_m')}M")
            print(f"      Assets: ${rec.get('total_assets_usd_m')}M")
    else:
        print(f"❌ Could not parse JSON from response")
        print(f"   Response: {response_text}")
        sys.exit(1)

except Exception as e:
    print(f"❌ Claude call failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 70)
print("  ✅ All tests passed! Claude OCR is working.")
print("=" * 70)
