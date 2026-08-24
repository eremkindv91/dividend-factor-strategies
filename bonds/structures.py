"""Composable bond structure and capability contracts for Bond Analytics v4."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class CouponType(StrEnum):
    FIXED = "FIXED"
    RUONIA_FLOAT = "RUONIA_FLOAT"
    KEY_RATE_FLOAT = "KEY_RATE_FLOAT"
    KBD_FLOAT = "KBD_FLOAT"
    OTHER_FLOAT = "OTHER_FLOAT"
    FIXED_THEN_RESET = "FIXED_THEN_RESET"
    ZERO = "ZERO"
    INDEX_LINKED = "INDEX_LINKED"


class PrincipalType(StrEnum):
    BULLET = "BULLET"
    AMORTIZING = "AMORTIZING"
    PERPETUAL = "PERPETUAL"
    VARIABLE_NOMINAL = "VARIABLE_NOMINAL"


class Seniority(StrEnum):
    SENIOR = "SENIOR"
    SUBORDINATED = "SUBORDINATED"
    PERPETUAL_SUBORDINATED = "PERPETUAL_SUBORDINATED"
    UNKNOWN = "UNKNOWN"


class AnalysisStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class CouponModel:
    type: CouponType
    current_rate_pct: float | None = None
    reference_index: str | None = None
    contractual_margin_bp: float | None = None
    floor_pct: float | None = None
    cap_pct: float | None = None
    reset_frequency: int | None = None
    observation_lag_days: int | None = None
    next_reset_date: str | None = None
    formula_confidence: str = "UNKNOWN"


@dataclass(frozen=True)
class PrincipalModel:
    type: PrincipalType
    maturity_date: str | None = None
    amortization_schedule: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Optionality:
    has_put: bool = False
    put_schedule: tuple[dict[str, Any], ...] = ()
    has_call: bool = False
    call_schedule: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class BondStructure:
    coupon_model: CouponModel
    principal_model: PrincipalModel
    optionality: Optionality = field(default_factory=Optionality)
    seniority: Seniority = Seniority.UNKNOWN
    coupon_deferrable: bool | None = None
    coupon_cumulative: bool | None = None
    guarantee_status: str | None = None
    government_support_mechanism: str | None = None
    qualified_only: bool = False
    legal_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CAPABILITY_KEYS = (
    "supports_ytm", "supports_current_yield", "supports_ytc", "supports_ytp",
    "supports_ytw", "supports_discount_margin", "supports_g_spread",
    "supports_z_spread", "supports_oas", "supports_modified_duration",
    "supports_effective_duration", "supports_spread_duration",
    "supports_cashflow_chart", "supports_reset_scenario", "supports_relative_value",
)


def capabilities_for(structure: BondStructure, status: AnalysisStatus) -> dict[str, bool]:
    """Return explicit frontend capabilities; OAS stays false without an option model."""
    c = {key: False for key in CAPABILITY_KEYS}
    if status == AnalysisStatus.UNSUPPORTED:
        return c
    coupon = structure.coupon_model.type
    principal = structure.principal_model.type
    option = structure.optionality
    deterministic_coupon = coupon in {CouponType.FIXED, CouponType.ZERO}
    deterministic_principal = principal in {PrincipalType.BULLET, PrincipalType.AMORTIZING}
    c.update({
        "supports_current_yield": coupon != CouponType.ZERO,
        "supports_cashflow_chart": True,
        "supports_relative_value": status == AnalysisStatus.FULL,
    })
    if deterministic_coupon and deterministic_principal:
        c.update({
            "supports_ytm": True,
            "supports_g_spread": True,
            "supports_z_spread": True,
            "supports_modified_duration": not option.has_call and not option.has_put,
            "supports_effective_duration": True,
            "supports_spread_duration": True,
        })
    if coupon in {
        CouponType.RUONIA_FLOAT, CouponType.KEY_RATE_FLOAT,
        CouponType.KBD_FLOAT, CouponType.OTHER_FLOAT,
    }:
        c.update({
            "supports_discount_margin": status == AnalysisStatus.FULL,
            "supports_effective_duration": status == AnalysisStatus.FULL,
            "supports_spread_duration": status == AnalysisStatus.FULL,
        })
    if option.has_put:
        c["supports_ytp"] = status == AnalysisStatus.FULL
        c["supports_ytw"] = status == AnalysisStatus.FULL
    if option.has_call:
        c["supports_ytc"] = status == AnalysisStatus.FULL
        c["supports_ytw"] = status == AnalysisStatus.FULL and c["supports_ytm"]
    if principal == PrincipalType.PERPETUAL:
        c["supports_ytm"] = False
        c["supports_modified_duration"] = False
        c["supports_ytc"] = option.has_call and status == AnalysisStatus.FULL
        c["supports_reset_scenario"] = (
            coupon == CouponType.FIXED_THEN_RESET and status == AnalysisStatus.FULL
        )
        c["supports_effective_duration"] = status == AnalysisStatus.FULL
        c["supports_spread_duration"] = status == AnalysisStatus.FULL
        c["supports_z_spread"] = option.has_call and status == AnalysisStatus.FULL
    return c


def legacy_structure_type(structure: BondStructure) -> str:
    if structure.principal_model.type == PrincipalType.PERPETUAL:
        return "STRUCTURED_OR_COMPLEX"
    if structure.coupon_model.type == CouponType.INDEX_LINKED:
        return "INDEX_LINKED"
    if structure.coupon_model.type in {
        CouponType.RUONIA_FLOAT, CouponType.KEY_RATE_FLOAT,
        CouponType.KBD_FLOAT, CouponType.OTHER_FLOAT,
    }:
        return "FLOATING"
    if structure.optionality.has_put or structure.optionality.has_call:
        return "OFFER"
    if structure.principal_model.type == PrincipalType.AMORTIZING:
        return "AMORTIZING"
    return "VANILLA_FIXED"


def structure_class(structure: BondStructure) -> str:
    if structure.principal_model.type == PrincipalType.PERPETUAL:
        return "PERPETUAL_RESET"
    if structure.seniority in {Seniority.SUBORDINATED, Seniority.PERPETUAL_SUBORDINATED}:
        return "SUBORDINATED"
    if structure.coupon_model.type in {
        CouponType.RUONIA_FLOAT, CouponType.KEY_RATE_FLOAT,
        CouponType.KBD_FLOAT, CouponType.OTHER_FLOAT,
    }:
        return "FLOATER"
    if structure.optionality.has_call:
        return "CALLABLE_FIXED"
    if structure.optionality.has_put:
        return "PUTTABLE_FIXED"
    if structure.principal_model.type == PrincipalType.AMORTIZING:
        return "AMORTIZING_FIXED"
    return "FIXED_BULLET"


def analysis_status_for(structure: BondStructure, *, has_price: bool, has_schedule: bool) -> AnalysisStatus:
    if not has_price:
        return AnalysisStatus.UNSUPPORTED
    coupon = structure.coupon_model
    principal = structure.principal_model
    if coupon.type in {CouponType.INDEX_LINKED} or principal.type == PrincipalType.VARIABLE_NOMINAL:
        return AnalysisStatus.PARTIAL
    if coupon.type in {
        CouponType.RUONIA_FLOAT, CouponType.KEY_RATE_FLOAT,
        CouponType.KBD_FLOAT, CouponType.OTHER_FLOAT, CouponType.FIXED_THEN_RESET,
    } and coupon.formula_confidence != "CONFIRMED":
        return AnalysisStatus.PARTIAL
    if principal.type == PrincipalType.PERPETUAL:
        return AnalysisStatus.FULL if structure.optionality.call_schedule else AnalysisStatus.PARTIAL
    return AnalysisStatus.FULL if has_schedule else AnalysisStatus.PARTIAL
