"""risk_scorer.py
Risk scoring module for counterparty assessment.

Calculates a 0-100 risk score based on:
1. Financial Health (40 points)
   - Debt-to-Equity ratio
   - Current Ratio (liquidity)
   - EBITDA Margin (profitability)
   - Revenue trend

2. Industry & Customer Type (25 points)
   - Sector volatility
   - Credit rating
   - Company stability

3. Payment History (20 points)
   - Days Past Due (if available)
   - Payment consistency
   - Default history

4. Ultimate Ownership (15 points)
   - Public vs Private
   - Ownership concentration
   - Regulatory risk
"""
import math
from typing import Optional


class RiskScorer:
    """Calculate counterparty risk scores."""

    # Financial health thresholds (lower is better)
    DE_RATIO_EXCELLENT = 0.5    # D/E < 0.5 = low risk
    DE_RATIO_GOOD = 1.0         # 0.5 - 1.0 = moderate
    DE_RATIO_CAUTION = 2.0      # 1.0 - 2.0 = elevated
    DE_RATIO_DANGER = 3.0       # > 3.0 = high risk

    CURRENT_RATIO_EXCELLENT = 2.0   # CR > 2.0 = excellent
    CURRENT_RATIO_GOOD = 1.5        # 1.5 - 2.0 = good
    CURRENT_RATIO_CAUTION = 1.0     # 1.0 - 1.5 = caution
    CURRENT_RATIO_DANGER = 0.8      # < 0.8 = critical

    EBITDA_MARGIN_EXCELLENT = 30   # > 30% = excellent
    EBITDA_MARGIN_GOOD = 20        # 20-30% = good
    EBITDA_MARGIN_CAUTION = 10     # 10-20% = caution
    EBITDA_MARGIN_DANGER = 0       # < 10% = danger

    # Industry risk weights
    SECTOR_RISK = {
        "oil & gas": 0.8,
        "trading": 0.7,
        "mining": 0.75,
        "energy": 0.8,
        "commodities": 0.7,
        "manufacturing": 0.6,
        "services": 0.5,
        "finance": 0.6,
        "utilities": 0.4,
        "tech": 0.5,
        "healthcare": 0.4,
        "retail": 0.6,
        "real estate": 0.65,
    }

    CREDIT_RATING_SCORES = {
        "aaa": 5, "aa+": 7, "aa": 8, "aa-": 10,
        "a+": 12, "a": 14, "a-": 16,
        "bbb+": 20, "bbb": 23, "bbb-": 26,
        "bb+": 32, "bb": 36, "bb-": 40,
        "b+": 50, "b": 56, "b-": 62,
        "ccc": 75, "cc": 85, "c": 92, "d": 99,
        "n/a": 50,
    }

    @staticmethod
    def calculate_score(
        company_name: str,
        country: str,
        sector: str,
        credit_rating: str,
        debt_to_equity: float,
        current_ratio: float,
        ebitda_margin_pct: float,
        revenue_usd_m: float = 0,
        total_debt_usd_m: float = 0,
        is_public: bool = False,
        days_past_due: int = 0,
        default_history: bool = False,
    ) -> dict:
        """
        Calculate comprehensive risk score (0-100, lower is better).

        Args:
            company_name: Company name
            country: Country of incorporation
            sector: Industry sector
            credit_rating: Credit rating (BBB, BB+, etc.)
            debt_to_equity: D/E ratio
            current_ratio: Current ratio
            ebitda_margin_pct: EBITDA margin %
            revenue_usd_m: Revenue in USD millions
            total_debt_usd_m: Total debt in USD millions
            is_public: Whether company is publicly traded
            days_past_due: Days past due on payments
            default_history: History of defaults

        Returns:
            Dict with:
            - score: Overall risk score (0-100)
            - financial_health_score: Financial component
            - industry_score: Industry/sector component
            - payment_score: Payment history component
            - ownership_score: Ownership structure component
            - breakdown: Detailed explanation
            - risk_level: LOW/MEDIUM/HIGH/CRITICAL
        """

        # Component 1: Financial Health (40 points)
        financial_score = RiskScorer._score_financial_health(
            debt_to_equity, current_ratio, ebitda_margin_pct
        ) * 40

        # Component 2: Industry & Credit (25 points)
        industry_score = RiskScorer._score_industry_credit(
            sector, credit_rating
        ) * 25

        # Component 3: Payment History (20 points)
        payment_score = RiskScorer._score_payment_history(
            days_past_due, default_history
        ) * 20

        # Component 4: Ownership Structure (15 points)
        ownership_score = RiskScorer._score_ownership(
            is_public, country, total_debt_usd_m
        ) * 15

        # Total score
        total_score = round(
            financial_score + industry_score + payment_score + ownership_score, 1
        )

        # Determine risk level
        risk_level = RiskScorer._score_to_risk_level(total_score)

        return {
            "score": total_score,
            "risk_level": risk_level,
            "financial_health_score": round(financial_score, 1),
            "industry_score": round(industry_score, 1),
            "payment_score": round(payment_score, 1),
            "ownership_score": round(ownership_score, 1),
            "breakdown": {
                "financial_health": RiskScorer._explain_financial(
                    debt_to_equity, current_ratio, ebitda_margin_pct
                ),
                "industry": RiskScorer._explain_industry(sector, credit_rating),
                "payment": RiskScorer._explain_payment(days_past_due, default_history),
                "ownership": RiskScorer._explain_ownership(is_public, country),
            },
        }

    @staticmethod
    def _score_financial_health(
        de_ratio: float, current_ratio: float, ebitda_margin: float
    ) -> float:
        """Score financial health (0.0 = excellent, 1.0 = critical)."""
        # D/E score (higher is worse)
        if de_ratio <= RiskScorer.DE_RATIO_EXCELLENT:
            de_score = 0.0
        elif de_ratio <= RiskScorer.DE_RATIO_GOOD:
            de_score = 0.2
        elif de_ratio <= RiskScorer.DE_RATIO_CAUTION:
            de_score = 0.5
        elif de_ratio <= RiskScorer.DE_RATIO_DANGER:
            de_score = 0.8
        else:
            de_score = 1.0

        # Current ratio score (lower is worse)
        if current_ratio >= RiskScorer.CURRENT_RATIO_EXCELLENT:
            cr_score = 0.0
        elif current_ratio >= RiskScorer.CURRENT_RATIO_GOOD:
            cr_score = 0.15
        elif current_ratio >= RiskScorer.CURRENT_RATIO_CAUTION:
            cr_score = 0.4
        elif current_ratio >= RiskScorer.CURRENT_RATIO_DANGER:
            cr_score = 0.75
        else:
            cr_score = 1.0

        # EBITDA margin score (lower is worse)
        if ebitda_margin >= RiskScorer.EBITDA_MARGIN_EXCELLENT:
            em_score = 0.0
        elif ebitda_margin >= RiskScorer.EBITDA_MARGIN_GOOD:
            em_score = 0.15
        elif ebitda_margin >= RiskScorer.EBITDA_MARGIN_CAUTION:
            em_score = 0.4
        elif ebitda_margin > RiskScorer.EBITDA_MARGIN_DANGER:
            em_score = 0.7
        else:
            em_score = 1.0

        # Average with weights: DE=50%, CR=30%, EM=20%
        return de_score * 0.5 + cr_score * 0.3 + em_score * 0.2

    @staticmethod
    def _score_industry_credit(sector: str, credit_rating: str) -> float:
        """Score industry and credit profile (0.0 = excellent, 1.0 = critical)."""
        # Sector risk
        sector_lower = (sector or "").lower()
        sector_risk = 0.5  # Default medium risk

        for sector_name, risk_weight in RiskScorer.SECTOR_RISK.items():
            if sector_name in sector_lower:
                sector_risk = risk_weight
                break

        # Credit rating risk
        rating_lower = (credit_rating or "n/a").lower().strip()
        credit_score = RiskScorer.CREDIT_RATING_SCORES.get(rating_lower, 50) / 100

        # Average: 60% sector, 40% credit
        return sector_risk * 0.6 + credit_score * 0.4

    @staticmethod
    def _score_payment_history(days_past_due: int, default_history: bool) -> float:
        """Score payment history (0.0 = excellent, 1.0 = critical)."""
        if default_history:
            return 0.95  # Near-critical

        if days_past_due <= 0:
            return 0.0  # Excellent
        elif days_past_due <= 30:
            return 0.2  # Minor delays
        elif days_past_due <= 60:
            return 0.4  # Moderate delays
        elif days_past_due <= 90:
            return 0.6  # Significant delays
        elif days_past_due <= 180:
            return 0.8  # Severe delays
        else:
            return 0.95  # Critical

    @staticmethod
    def _score_ownership(
        is_public: bool, country: str, total_debt: float
    ) -> float:
        """Score ownership structure (0.0 = excellent, 1.0 = critical)."""
        # Public companies are generally lower risk
        public_score = 0.2 if is_public else 0.5

        # Country risk (simplified)
        country_lower = (country or "").lower()
        country_risk = 0.3  # Default low country risk

        high_risk_countries = [
            "venezuela", "yemen", "syria", "sudan", "somalia",
            "liberia", "central african republic"
        ]
        if any(c in country_lower for c in high_risk_countries):
            country_risk = 0.8

        # Debt size risk (larger debts = more risk to manage)
        if total_debt <= 100:
            debt_risk = 0.0
        elif total_debt <= 1000:
            debt_risk = 0.2
        elif total_debt <= 5000:
            debt_risk = 0.4
        else:
            debt_risk = 0.6

        # Average: 50% public/private, 30% country, 20% debt
        return public_score * 0.5 + country_risk * 0.3 + debt_risk * 0.2

    @staticmethod
    def _score_to_risk_level(score: float) -> str:
        """Convert numeric score to risk level."""
        if score <= 25:
            return "LOW"
        elif score <= 50:
            return "MEDIUM"
        elif score <= 75:
            return "HIGH"
        else:
            return "CRITICAL"

    @staticmethod
    def _explain_financial(de: float, cr: float, em: float) -> str:
        """Explain financial health assessment."""
        parts = []

        if de <= 1.0:
            parts.append(f"Strong leverage position (D/E: {de:.2f})")
        else:
            parts.append(f"Elevated leverage (D/E: {de:.2f})")

        if cr >= 1.5:
            parts.append(f"Good liquidity (CR: {cr:.2f})")
        else:
            parts.append(f"Tight liquidity (CR: {cr:.2f})")

        if em >= 20:
            parts.append(f"Strong profitability ({em:.1f}% margin)")
        else:
            parts.append(f"Thin margins ({em:.1f}%)")

        return " | ".join(parts)

    @staticmethod
    def _explain_industry(sector: str, rating: str) -> str:
        """Explain industry/credit assessment."""
        return f"{sector or 'N/A'} sector | {rating or 'Unrated'}"

    @staticmethod
    def _explain_payment(days_past_due: int, default: bool) -> str:
        """Explain payment history assessment."""
        if default:
            return "Prior default on record"
        if days_past_due <= 0:
            return "Payment current"
        else:
            return f"{days_past_due} days past due"

    @staticmethod
    def _explain_ownership(is_public: bool, country: str) -> str:
        """Explain ownership structure assessment."""
        status = "Public" if is_public else "Private"
        return f"{status} company | {country or 'Unknown'} jurisdiction"
