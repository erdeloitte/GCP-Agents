#!/usr/bin/env python3
"""
Check if .env file is being loaded correctly.
"""
import os
import sys

print("=" * 70)
print("  .ENV File Checker")
print("=" * 70)

# Check 1: Does .env file exist?
print("\n1. Checking if .env file exists...")
env_path = os.path.join(os.getcwd(), ".env")
if os.path.exists(".env"):
    print(f"✅ .env file found at: {env_path}")
    with open(".env", "r") as f:
        content = f.read()
    print(f"   File size: {len(content)} bytes")
    print(f"   Content preview:")
    for line in content.split("\n")[:10]:
        if "ANTHROPIC" in line:
            # Show key but hide actual value
            if "=" in line:
                key = line.split("=")[0]
                print(f"   {key}=sk-...{line.split('=')[1][-5:] if '=' in line else ''}")
        elif line.strip() and not line.startswith("#"):
            print(f"   {line}")
else:
    print(f"❌ .env file NOT found")
    print(f"   Expected at: {env_path}")
    print(f"   Current directory: {os.getcwd()}")
    print(f"   Files in current directory:")
    for f in os.listdir(".")[:10]:
        print(f"      {f}")
    sys.exit(1)

# Check 2: Load with dotenv
print("\n2. Loading with python-dotenv...")
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ python-dotenv loaded successfully")
except ImportError:
    print("❌ python-dotenv not installed")
    print("   Fix: pip install python-dotenv")
    sys.exit(1)

# Check 3: Check ANTHROPIC_API_KEY
print("\n3. Checking ANTHROPIC_API_KEY in environment...")
api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

if api_key:
    print(f"✅ ANTHROPIC_API_KEY found in environment")
    print(f"   Starts with: {api_key[:20]}")
    print(f"   Ends with: {api_key[-5:]}")
    print(f"   Length: {len(api_key)}")
    if api_key.startswith("sk-"):
        print(f"✅ Looks like a valid Anthropic key")
    else:
        print(f"⚠️  Doesn't start with 'sk-' (unexpected)")
else:
    print(f"❌ ANTHROPIC_API_KEY not found in environment")
    print(f"   Make sure .env has: ANTHROPIC_API_KEY=sk-ant-v1-...")
    sys.exit(1)

# Check 4: Check GEMINI_API_KEY too
print("\n4. Checking GEMINI_API_KEY...")
gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
if gemini_key:
    print(f"✅ GEMINI_API_KEY found: {gemini_key[:20]}...")
else:
    print(f"⚠️  GEMINI_API_KEY not found (optional)")

# Check 5: Check other important vars
print("\n5. Checking other environment variables...")
vars_to_check = ["BQ_DATASET", "BUCKET", "PORT"]
for var in vars_to_check:
    value = os.getenv(var, "").strip()
    status = f"✅ {var}={value}" if value else f"⚠️  {var} not set"
    print(f"   {status}")

print("\n" + "=" * 70)
print("  ✅ Setup looks good!")
print("=" * 70)
