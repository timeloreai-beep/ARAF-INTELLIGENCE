import math
from dataclasses import dataclass
from scipy.stats import norm


@dataclass(frozen=True)
class BlackScholesGreeks:
    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


def black_scholes(
    spot: float,
    strike: float,
    annualized_rate: float,
    volatility: float,
    time_to_expiry_years: float,
    dividend_yield: float = 0.0,
    option_type: str = "call",
) -> BlackScholesGreeks:
    if min(spot, strike, volatility, time_to_expiry_years) <= 0:
        raise ValueError("spot, strike, volatility and expiry must be positive")
    if option_type not in {"call", "put"}:
        raise ValueError("option_type must be call or put")

    root_t = math.sqrt(time_to_expiry_years)
    d1 = (
        math.log(spot / strike)
        + (annualized_rate - dividend_yield + 0.5 * volatility * volatility)
        * time_to_expiry_years
    ) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    discounted_spot = spot * math.exp(-dividend_yield * time_to_expiry_years)
    discounted_strike = strike * math.exp(-annualized_rate * time_to_expiry_years)
    pdf_d1 = norm.pdf(d1)
    cdf_d1 = norm.cdf(d1)
    cdf_d2 = norm.cdf(d2)

    if option_type == "call":
        price = discounted_spot * cdf_d1 - discounted_strike * cdf_d2
        delta = math.exp(-dividend_yield * time_to_expiry_years) * cdf_d1
        rho = strike * time_to_expiry_years * math.exp(-annualized_rate * time_to_expiry_years) * cdf_d2
    else:
        price = discounted_strike * norm.cdf(-d2) - discounted_spot * norm.cdf(-d1)
        delta = math.exp(-dividend_yield * time_to_expiry_years) * (cdf_d1 - 1)
        rho = -strike * time_to_expiry_years * math.exp(-annualized_rate * time_to_expiry_years) * norm.cdf(-d2)

    gamma = math.exp(-dividend_yield * time_to_expiry_years) * pdf_d1 / (spot * volatility * root_t)
    theta = (
        -(spot * volatility * math.exp(-dividend_yield * time_to_expiry_years) * pdf_d1)
        / (2 * root_t)
        - annualized_rate * discounted_strike * (cdf_d2 if option_type == "call" else norm.cdf(-d2))
        + dividend_yield * discounted_spot * (cdf_d1 if option_type == "call" else norm.cdf(-d1))
    ) / 365
    vega = spot * math.exp(-dividend_yield * time_to_expiry_years) * pdf_d1 * root_t / 100

    return BlackScholesGreeks(price, delta, gamma, theta, vega, rho)
