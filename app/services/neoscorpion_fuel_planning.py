from decimal import Decimal


def plan_fuel_by_tank(
    aircraft_type,
    required_lbs,
    *,
    remaining_lbs_by_tank=None,
    actual_lbs_by_tank=None,
    apu_running=None,
    apu_allowance_lbs=None,
    apu_source_tank_code=None,
):
    """Return the Hanzo-equivalent planned pounds by tank, or None if incomplete."""
    if required_lbs is None or apu_running is None:
        return None

    required = _decimal(required_lbs)
    remaining = {
        code: _decimal(value or 0)
        for code, value in (remaining_lbs_by_tank or {}).items()
    }
    actual = {
        code: _decimal(value or 0)
        for code, value in (actual_lbs_by_tank or {}).items()
    }
    planners = {
        "B757": _plan_b757,
        "A300": _plan_a300,
        "B767ER": _plan_b767er,
        "B747-400": _plan_b747_400,
        "B747-8": _plan_b747_8,
    }
    planner = planners.get(aircraft_type)
    if planner is None:
        return None

    planned = planner(required, remaining, actual)
    if apu_running:
        if (
            apu_allowance_lbs is None
            or apu_source_tank_code not in planned
        ):
            return None
        planned[apu_source_tank_code] += _decimal(apu_allowance_lbs)
    return planned


def _plan_b757(required, remaining, _actual):
    return _plan_three_tank_wing_aircraft(
        required,
        remaining,
        wing_max=Decimal("14600"),
        wing_threshold=Decimal("29200"),
    )


def _plan_b767er(required, remaining, _actual):
    return _plan_three_tank_wing_aircraft(
        required,
        remaining,
        wing_max=Decimal("40200"),
        wing_threshold=Decimal("80400"),
    )


def _plan_three_tank_wing_aircraft(
    required,
    remaining,
    *,
    wing_max,
    wing_threshold,
):
    center_remaining = remaining.get("ctr", Decimal("0"))
    if center_remaining > 0:
        left = (
            wing_max
            if required >= wing_threshold + center_remaining
            else (required - center_remaining) / 2
        )
    else:
        left = wing_max if required >= wing_threshold else required / 2
    center = (
        center_remaining
        if required <= wing_threshold
        else required - left - left
    )
    return {"left": left, "ctr": center, "right": left}


def _plan_a300(required, remaining, _actual):
    center_remaining = remaining.get("ctr", Decimal("0"))
    outboard = (
        Decimal("8200")
        if required >= Decimal("16400")
        else (required - Decimal("4100")) / 2
    )
    if center_remaining > 0 and required < Decimal("78600"):
        inboard = (
            (required - outboard - outboard) / 2
        ) - (center_remaining / 2)
    else:
        inboard = (
            Decimal("31100")
            if required >= Decimal("78600")
            else (required - outboard - outboard) / 2
        )
    center = (
        Decimal("31100")
        if required >= Decimal("109700")
        else required - outboard - inboard - inboard - outboard
    )
    trim = (
        Decimal("0")
        if required <= Decimal("109700")
        else required - outboard - inboard - center - inboard - outboard
    )
    return {
        "l_out": outboard,
        "l_in": inboard,
        "ctr": center,
        "r_in": inboard,
        "r_out": outboard,
        "tt": trim,
    }


def _plan_b747_400(required, _remaining, actual):
    left_outboard = (
        Decimal("29292")
        if required >= Decimal("117168")
        else required / 4
    )
    left_inboard = (
        Decimal("84058")
        if required >= Decimal("244410")
        else actual.get("main_l_in", Decimal("0"))
    )
    main_total = (left_outboard + left_inboard) * 2
    reserve = (
        Decimal("8857")
        if required >= Decimal("163510")
        else (required - main_total) / 2
    )
    center = required - main_total - reserve - reserve
    return {
        "main_l_out": left_outboard,
        "main_l_in": left_inboard,
        "main_r_in": left_inboard,
        "main_r_out": left_outboard,
        "reserve_2_l": reserve,
        "reserve_3_r": reserve,
        "center_wing": center,
    }


def _plan_b747_8(required, _remaining, actual):
    left_outboard = (
        Decimal("35644")
        if required >= Decimal("183688")
        else actual.get("main_l_out", Decimal("0"))
    )
    left_inboard = (
        Decimal("96681")
        if required >= Decimal("285206")
        else actual.get("main_l_in", Decimal("0"))
    )
    reserve = (
        Decimal("10278")
        if required >= Decimal("94712")
        else (required - Decimal("53600")) / 4
    )
    center = required - ((left_outboard + left_inboard) * 2) - (reserve * 2)
    return {
        "main_l_out": left_outboard,
        "main_l_in": left_inboard,
        "main_r_in": left_inboard,
        "main_r_out": left_outboard,
        "reserve_1_l": reserve,
        "reserve_4_r": reserve,
        "center_wing": center,
    }


def _decimal(value):
    return Decimal(str(value))
