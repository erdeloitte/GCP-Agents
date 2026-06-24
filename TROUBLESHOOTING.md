# Troubleshooting: "No records extracted" Error

## Quick Diagnosis

### Step 1: Run the diagnostic script
```bash
python DIAGNOSTIC.py
```

This checks:
- ✅ ANTHROPIC_API_KEY is set
- ✅ All packages installed
- ✅ Claude API connection works
- ✅ OCR functions load
- ✅ Claude can extract records

---

## Most Common Issues

### Issue 1: ANTHROPIC_API_KEY not set ⚠️

**Error symptoms**:
- Upload returns: `{"debug": {"claude_attempted": false}}`
- Server logs show: `[CLAUDE] Error: ANTHROPIC_API_KEY not set`

**Fix**:
```bash
# 1. Set the environment variable
export ANTHROPIC_API_KEY=sk-your-actual-key-here

# 2. Verify it's set
echo $ANTHROPIC_API_KEY
# Should print: sk-...

# 3. Restart your app
```

**Check in code**:
```python
import os
api_key = os.getenv("ANTHROPIC_API_KEY")
print(f"API Key set: {bool(api_key)}")
print(f"Starts with sk-: {api_key.startswith('sk-') if api_key else False}")
```

---

### Issue 2: Wrong API Key format ⚠️

**Error symptoms**:
- Upload returns error about authentication
- Claude API returns 401 Unauthorized

**Fix**:
```bash
# Check your key format
echo $ANTHROPIC_API_KEY

# Should look like: sk-ant-v1-abcdef123456...
# NOT: sk-..  (incomplete)
# NOT: somestring (no sk- prefix)
```

**Where to get key**:
1. Go to https://console.anthropic.com
2. Sign in
3. Click "API Keys" in left sidebar
4. Create or copy your key
5. Set it: `export ANTHROPIC_API_KEY=sk-ant-v1-...`

---

### Issue 3: File format not supported ❌

**Error symptoms**:
- Upload works but no records extracted
- Diagnostic shows Claude connected OK
- Server logs show: `[CLAUDE] Returned 0 records`

**Supported formats**:
- ✅ Excel (XLSX, XLS)
- ✅ CSV (comma, semicolon, tab-separated)
- ✅ PDF
- ✅ Images (PNG, JPG, GIF)
- ✅ Plain text (TXT)

**File content requirements**:
- Must have company/entity name
- Must have at least 2 financial metrics (revenue, EBITDA, assets, debt)
- Clear layout (headers help but not required)

**Test with this simple CSV**:
```csv
company_name,revenue_usd_m,ebitda_usd_m,assets_usd_m,debt_usd_m
Shell,400000,120000,2500000,800000
Glencore,180000,45000,95000,35000
```

If this works, your setup is fine. If not, check Issue #1.

---

### Issue 4: Packages not installed ❌

**Error symptoms**:
- Server logs show: `ModuleNotFoundError: No module named 'anthropic'`
- Diagnostic shows ❌ for imports

**Fix**:
```bash
# Install required packages
pip install anthropic

# Verify installation
python -c "from anthropic import Anthropic; print('OK')"
```

---

### Issue 5: Network/Firewall blocking Claude API 🔒

**Error symptoms**:
- Diagnostic shows Claude connection failed
- Error mentions: timeout, refused, unreachable

**Fix**:
```bash
# Test if api.anthropic.com is reachable
curl -I https://api.anthropic.com
# Should return: HTTP/1.1 301 Moved Permanently (or similar)

# If timeout/refused:
# - Check your firewall/proxy settings
# - Check if you need VPN
# - Check if behind corporate firewall
```

**In corporate environment**:
- Talk to IT about allowing api.anthropic.com
- Might need to configure proxy

---

### Issue 6: File is too large 📦

**Error symptoms**:
- Upload hangs or times out
- Diagnostic works fine
- But uploading large file fails

**Limits**:
- Claude Vision: Files up to 20MB (PDFs), images up to 5MB
- Text content: up to ~100,000 tokens

**Fix**:
- Split large PDF into pages
- Compress images before uploading
- Extract text from PDF first

---

### Issue 7: BigQuery table missing or wrong schema 📊

**Error symptoms**:
- Upload says "success" but no data appears in BQ
- Error in logs about `treasury_analytics.deposits`

**Fix**:
```sql
-- Create the deposits table if missing
CREATE TABLE IF NOT EXISTS `project.treasury_analytics.deposits` (
  counterparty_name STRING,
  amount_usd FLOAT64,
  currency STRING,
  deposit_date DATE,
  transaction_id STRING
);

-- Verify counterparties table exists
SELECT COUNT(*) FROM `project.treasury_analytics.counterparties`;

-- Verify you can query it
SELECT * FROM `project.treasury_analytics.counterparties` LIMIT 1;
```

---

## Step-by-Step Debugging

### 1. Check server logs
```bash
# Look for extraction details
tail -f your-app.log | grep "\[OCR\]\|\[CLAUDE\]\|\[BQ\]"

# Expected successful sequence:
# [OCR] Standard returned 0 records
# [OCR] Standard OCR empty → Triggering Claude enhancement...
# [CLAUDE] Returned 1 records, method: claude_vision, confidence: high
# [BQ] Inserted 1 records
# [UPLOAD] Complete: 1 records
```

### 2. Check response JSON
```javascript
// In browser console (F12)
// After upload, check the response:
fetch('/api/upload', {method: 'POST', body: fd})
  .then(r => r.json())
  .then(d => console.log(JSON.stringify(d, null, 2)));

// Should show:
// {
//   "status": "success",
//   "extraction_method": "claude_vision" or "standard_ocr",
//   "claude_used": true,
//   "claude_confidence": "high",
//   "ingested": 1,
//   ...
// }
```

### 3. Test Claude directly
```python
# python3 test_claude.py
from claude_ocr_enhance import enhance_with_claude

with open("your_file.pdf", "rb") as f:
    content = f.read()

result = enhance_with_claude(content, "test.pdf", [])
print(f"Records: {len(result.get('records'))}")
print(f"Error: {result.get('error')}")
print(f"Confidence: {result.get('confidence')}")
```

### 4. Test with simpler file
```bash
# Create a minimal test CSV
cat > test.csv << 'EOF'
company_name,revenue_usd_m,ebitda_usd_m
TestCorp,1000,250
EOF

# Upload this
# Should work - uses standard OCR
```

---

## Error Response Codes

### 422 - No records extracted
```json
{
  "error": "No records extracted",
  "debug": {
    "standard_ocr_ran": true,
    "claude_attempted": true,
    "claude_error": "ANTHROPIC_API_KEY not set",
    "file_size_bytes": 12345,
    "file_name": "document.pdf"
  }
}
```

**Fix**: Look at `claude_error` field for specific problem

### 202 - Duplicates detected
```json
{
  "status": "duplicates_detected",
  "requires_approval": true,
  "duplicates": [
    {"company_name": "Shell", "action_required": "review_and_update"}
  ]
}
```

**Fix**: Click "✓ Update Records" button in UI, or POST to `/api/approve-duplicate`

### 400 - Bad request
```json
{
  "error": "No file in request"
}
```

**Fix**: Make sure file is attached to form

---

## Contact Support

If diagnostic shows all ✅ but upload still fails:

1. **Check error response** in browser (F12 → Network tab)
2. **Check server logs** for detailed error messages
3. **Run diagnostic script** and save output
4. **Verify file content** is valid financial data

---

## Quick Checklist

- [ ] `echo $ANTHROPIC_API_KEY` shows `sk-ant-v1-...`
- [ ] `python DIAGNOSTIC.py` shows all ✅
- [ ] Test file has company name + 2+ financial metrics
- [ ] Server logs show `[CLAUDE]` entries (not `[ERROR]`)
- [ ] BigQuery tables exist: counterparties, deposits
- [ ] No network/firewall blocking api.anthropic.com

---

**Last Updated**: 2026-06-21
