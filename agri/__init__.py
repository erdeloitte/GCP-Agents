"""agri — deterministic core for the agricultural derivatives prototype.

Everything in this package is plain, testable Python with no Flask and no
LLM imports. Valuations, exposures, limit utilisation and exception rules are
computed here and only here. AI features (see agri_api.py) may *explain*
results produced by this package but never produce or change them.

PROTOTYPE: all reference and market data is synthetic demo data.
"""
