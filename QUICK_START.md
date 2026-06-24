# Quick Start Guide - Enhanced Treasury Platform

## 🚀 What's New in This Version

### 1. **Smart OCR with Claude Vision** 🤖
Upload PDFs or images with **any format** - Claude will intelligently extract financial data

**How to use:**
1. Click "Click to upload" in the **Onboard Counterparty** section
2. Upload a PDF, image, or Excel file with financial data
3. If the layout is non-standard, Claude will handle it automatically
4. If it's a duplicate counterparty:
   - Review the warning message
   - Click "✓ Update Records" to approve the new data
   - Or click "✗ Cancel" to skip

**What it detects:**
- Company name, country, sector
- Revenue, EBITDA, net income
- Total assets, debt, current ratio
- Credit ratings
- Multiple counterparties in one file

---

### 2. **Live Counterparty Indicators** 📊
See detailed metrics for any counterparty with one click

**How to use:**
1. Enter a counterparty name (e.g., "Glencore", "Shell")
2. Click any risk assessment button (Market Risk, Credit Risk, etc.)
3. A new **"Counterparty Indicators"** card appears in the right sidebar
4. View:
   - **D/E Ratio** → Leverage level
   - **Net Margin** → Profitability
   - **Cash/Liquidity** → Liquidity position
   - **Stock Price** → Market valuation (public companies)
   - **Total Deposits** → Treasury position

---

### 3. **Enhanced Market Data Assistant** 💬
The chat now understands deposits and can show you treasury data

**Try these questions:**
- "Show me deposit data"
- "What's our total exposure by counterparty?"
- "Chart the deposits"
- "Which counterparties have the most deposits?"
- "What's the average deposit size?"

**How it works:**
- Detects if you're asking about deposits/charts
- Automatically queries the database
- Shows formatted summary with counterparty breakdown

---

### 4. **Modern Deloitte-Branded UI** 🎨
- **AI Logo** at top right
- **Deloitte Green** (#86BC25) throughout
- Smooth **shadows and gradients**
- Better **hover effects**
- Cleaner **typography and spacing**

---

## 🔧 Configuration

### Environment Variables Needed
```bash
ANTHROPIC_API_KEY=sk-your-key-here       # Required for Claude OCR
GEMINI_API_KEY=your-gemini-key           # For agents
BQ_DATASET=treasury_analytics            # BigQuery dataset
BUCKET=your-gcs-bucket                   # Google Cloud Storage
```

### BigQuery Table Setup
Make sure you have a `deposits` table in your BigQuery dataset:

```sql
CREATE TABLE `project.treasury_analytics.deposits` (
  counterparty_name STRING,
  amount_usd FLOAT64,
  currency STRING,
  deposit_date DATE,
  transaction_id STRING,
  -- Add other columns as needed
);
```

---

## 📝 Example Workflows

### Workflow 1: Onboard a New Counterparty
```
1. Get financial statement (PDF, Excel, Word, or image)
2. Upload to dashboard
3. If new → Auto-ingests into database
4. If duplicate → Review and approve update
5. Select counterparty name
6. Click "Market Risk" or other agent
7. View all indicators in sidebar
```

### Workflow 2: Analyze Deposit Position
```
1. Open chat: "Market Data Assistant" section
2. Ask: "Show me top 10 counterparties by deposits"
3. Get formatted list with amounts and counts
4. Ask: "What's our total treasury on deposit?"
5. Get summary from database
```

### Workflow 3: Risk Assessment
```
1. Select a counterparty from the dropdown
2. Click "Comprehensive Assessment — All Agents"
3. View market, credit, and liquidity risk memos
4. Check indicators on the right:
   - D/E ratio tells you leverage risk
   - Net margin shows earnings quality
   - Cash % shows liquidity buffer
5. See deposit position at bottom
```

---

## 🎯 Key Features

| Feature | Location | Status |
|---------|----------|--------|
| Claude Vision OCR | Upload card | ✅ Active |
| Duplicate detection | Upload flow | ✅ Active |
| Counterparty indicators | Right sidebar | ✅ Active (shows on agent run) |
| Deposit data retrieval | Chat assistant | ✅ Active |
| Chart data generation | API endpoint | ✅ Ready |
| Deloitte branding | Throughout | ✅ Applied |

---

## 🐛 Troubleshooting

### Upload fails with "No records extracted"
- Check file has these columns: company_name, revenue, ebitda, assets, debt
- Or upload a PDF/image - Claude will extract automatically
- Make sure file isn't corrupted

### Counterparty indicators not showing
- Make sure counterparty exists in database
- Run an agent assessment (click Market Risk, Credit Risk, etc.)
- Wait 2-3 seconds for indicators to load

### Chat says "No data" for deposits
- Check that deposits table exists in BigQuery
- Verify table has the right column names
- Ensure there are records for that counterparty

### AI logo not showing
- Make sure `AI_logo.png` is in the same directory as `dashboard.html`
- Check file permissions
- Try refreshing the page

---

## 📚 API Reference

### Upload with OCR
```
POST /api/upload
Body: FormData with "file" (PDF/Excel/CSV)
Response: 
  - 200: {ingested: N, records: [...], extraction_method: "..."}
  - 202: {status: "duplicates_detected", duplicates: [...], requires_approval: true}
```

### Approve Duplicate
```
POST /api/approve-duplicate
Body: {records: [...], filename: "..."}
Response: {status: "approved", ingested: N}
```

### Get Indicators
```
GET /api/indicators/{counterparty_name}
Response: {
  debt_to_equity: X.XX,
  net_margin_pct: X.X,
  cash_equivalents_estimate_pct: X.X,
  total_deposits_usd: XXXXXX,
  ...
}
```

### Get Deposits Chart Data
```
GET /api/deposits-chart-data
Response: {
  data: [
    {counterparty: "Shell", amount: 1000000, count: 50},
    ...
  ]
}
```

### Chat with Context
```
POST /api/chat
Body: {question: "Show me deposits", company: null}
Response: {answer: "..."}
```

---

## ✨ Pro Tips

1. **Non-standard documents?** Just upload - Claude handles it!
2. **Checking leverage?** Look at D/E ratio in indicators
3. **Comparing margins?** Check Net Margin % across counterparties
4. **Treasury position?** Ask chat "Show deposits" for quick overview
5. **Risk dashboard?** Click "Comprehensive Assessment" for full memo

---

## 🤝 Support

For issues or questions:
1. Check IMPLEMENTATION_SUMMARY.md for technical details
2. Review API responses in browser dev tools (F12)
3. Check BigQuery table schema
4. Verify environment variables are set

---

**Version**: 2.0 - Claude OCR + Indicators + Deposits  
**Last Updated**: 2026-06-20  
**Status**: Production Ready ✅
