(() => {
    "use strict";

    const planThreeTank = (required, remaining, wingMax, wingThreshold) => {
        const centerRemaining = Number(remaining.ctr || 0);
        let left;
        if (centerRemaining > 0) {
            left = required >= wingThreshold + centerRemaining
                ? wingMax
                : (required - centerRemaining) / 2;
        } else {
            left = required >= wingThreshold ? wingMax : required / 2;
        }
        const center = required <= wingThreshold
            ? centerRemaining
            : required - left - left;
        return {left, ctr: center, right: left};
    };

    const basePlan = (aircraftType, required, remaining = {}, actual = {}) => {
        if (!Number.isFinite(required)) return null;
        if (aircraftType === "B757") {
            return planThreeTank(required, remaining, 14.6, 29.2);
        }
        if (aircraftType === "B767ER") {
            return planThreeTank(required, remaining, 40.2, 80.4);
        }
        if (aircraftType === "A300") {
            const centerRemaining = Number(remaining.ctr || 0);
            const outboard = required >= 16.4 ? 8.2 : (required - 4.1) / 2;
            let inboard;
            if (centerRemaining > 0 && required < 78.6) {
                inboard = ((required - outboard - outboard) / 2)
                    - (centerRemaining / 2);
            } else {
                inboard = required >= 78.6
                    ? 31.1
                    : (required - outboard - outboard) / 2;
            }
            const center = required >= 109.7
                ? 31.1
                : required - outboard - inboard - inboard - outboard;
            const trim = required <= 109.7
                ? 0
                : required - outboard - inboard - center - inboard - outboard;
            return {
                l_out: outboard,
                l_in: inboard,
                ctr: center,
                r_in: inboard,
                r_out: outboard,
                tt: trim,
            };
        }
        if (aircraftType === "B747-400") {
            const leftOutboard = required >= 117.168 ? 29.292 : required / 4;
            const leftInboard = required >= 244.41
                ? 84.058
                : Number(actual.main_l_in || 0);
            const mainTotal = (leftOutboard + leftInboard) * 2;
            const reserve = required >= 163.51
                ? 8.857
                : (required - mainTotal) / 2;
            return {
                main_l_out: leftOutboard,
                main_l_in: leftInboard,
                main_r_in: leftInboard,
                main_r_out: leftOutboard,
                reserve_2_l: reserve,
                reserve_3_r: reserve,
                center_wing: required - mainTotal - reserve - reserve,
            };
        }
        if (aircraftType === "B747-8") {
            const leftOutboard = required >= 183.688
                ? 35.644
                : Number(actual.main_l_out || 0);
            const leftInboard = required >= 285.206
                ? 96.681
                : Number(actual.main_l_in || 0);
            const reserve = required >= 94.712
                ? 10.278
                : (required - 53.6) / 4;
            return {
                main_l_out: leftOutboard,
                main_l_in: leftInboard,
                main_r_in: leftInboard,
                main_r_out: leftOutboard,
                reserve_1_l: reserve,
                reserve_4_r: reserve,
                center_wing: required
                    - ((leftOutboard + leftInboard) * 2)
                    - (reserve * 2),
            };
        }
        return null;
    };

    const planFuelByTank = ({
        aircraftType,
        required,
        remaining,
        actual,
        apuRunning,
        apuAllowance,
        apuSource,
    }) => {
        if (apuRunning === null) return null;
        const planned = basePlan(aircraftType, required, remaining, actual);
        if (!planned) return null;
        if (apuRunning) {
            if (!Number.isFinite(apuAllowance) || !(apuSource in planned)) {
                return null;
            }
            planned[apuSource] += apuAllowance;
        }
        return planned;
    };

    const calculateApuAllowance = ({
        plannedDepartureUtc,
        windowMinutes,
        confirmedAtMs,
        rate,
    }) => {
        const departureMs = Date.parse(plannedDepartureUtc);
        if (!Number.isFinite(departureMs) || !Number.isFinite(rate) || rate < 0) {
            return null;
        }
        const effectiveDepartureMs = departureMs + (windowMinutes * 60000);
        const remainingHours = Math.max(
            0,
            (effectiveDepartureMs - confirmedAtMs) / 3600000
        );
        const rawAllowance = remainingHours * rate;
        return Math.ceil((rawAllowance * 10) - 1e-9) / 10;
    };

    const remainingReadingsComplete = (tankCodes, remaining) => (
        tankCodes.length > 0
        && tankCodes.every((tankCode) => Number.isFinite(remaining[tankCode]))
    );

    const api = {
        basePlan,
        calculateApuAllowance,
        planFuelByTank,
        remainingReadingsComplete,
    };
    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
    if (typeof window !== "undefined") {
        window.NeoScorpionFuelPlanning = api;
    }
    if (typeof document === "undefined") return;

    const numberOrNull = (value) => {
        if (String(value ?? "").trim() === "") return null;
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    };
    const displayFuel = (value) => (
        Number.isFinite(value) ? `${value.toFixed(1)} K LBS` : "INCOMPLETE"
    );

    document.querySelectorAll("[data-fuel-planning-form]").forEach((form) => {
        const card = form.closest("[data-fuel-assignment-id]");
        const apuRunningInput = form.querySelector("[data-apu-running]");
        const apuSourceInput = form.querySelector("[data-apu-source]");
        const apuSourceWrap = form.querySelector("[data-apu-source-wrap]");
        const plannedEntryCue = form.querySelector("[data-planned-entry-cue]");
        if (!card || !apuRunningInput || !apuSourceInput) return;

        const initialApuRunning = form.dataset.initialApuRunning;
        const persistedAllowanceLbs = numberOrNull(
            form.dataset.persistedApuAllowanceLbs
        );
        const requiredLbs = numberOrNull(form.dataset.requiredLbs);
        const required = requiredLbs === null ? null : requiredLbs / 1000;
        const rate = Number(form.dataset.effectiveApuRate);
        const windowMinutes = Number(form.dataset.windowMinutes || 0);

        const update = () => {
            const selectedApu = apuRunningInput.value;
            const apuRunning = selectedApu === "yes"
                ? true
                : selectedApu === "no"
                ? false
                : null;
            const sourceRequired = apuRunning === true;
            apuSourceWrap.hidden = !sourceRequired;
            apuSourceInput.disabled = !sourceRequired;
            apuSourceInput.required = sourceRequired;
            if (!sourceRequired) apuSourceInput.value = "";

            let allowance = null;
            if (apuRunning === false) {
                allowance = 0;
            } else if (apuRunning === true) {
                allowance = (
                    initialApuRunning === "yes" && persistedAllowanceLbs !== null
                )
                    ? persistedAllowanceLbs / 1000
                    : calculateApuAllowance({
                        plannedDepartureUtc: form.dataset.plannedDepartureUtc,
                        windowMinutes,
                        confirmedAtMs: Date.now(),
                        rate,
                    });
            }

            const remaining = {};
            const actual = {};
            const tankCodes = [];
            form.querySelectorAll("[data-fuel-reading]").forEach((input) => {
                const value = numberOrNull(input.value);
                const target = input.dataset.fuelReading === "remaining"
                    ? remaining
                    : actual;
                target[input.dataset.tankCode] = (
                    input.dataset.fuelReading === "actual" && value === null
                        ? 0
                        : value
                );
                if (input.dataset.fuelReading === "remaining") {
                    tankCodes.push(input.dataset.tankCode);
                }
            });
            const remainingComplete = remainingReadingsComplete(tankCodes, remaining);
            const planned = remainingComplete
                ? planFuelByTank({
                    aircraftType: form.dataset.aircraftType,
                    required,
                    remaining,
                    actual,
                    apuRunning,
                    apuAllowance: allowance,
                    apuSource: apuSourceInput.value,
                })
                : null;
            if (plannedEntryCue) plannedEntryCue.hidden = remainingComplete;

            card.querySelector("[data-apu-allowance-output]").textContent = displayFuel(
                allowance
            );
            card.querySelector("[data-fueling-target-output]").textContent = displayFuel(
                required !== null && allowance !== null ? required + allowance : null
            );
            const selectedSourceOption = apuSourceInput.selectedOptions?.[0];
            card.querySelector("[data-apu-source-output]").textContent = (
                sourceRequired && apuSourceInput.value
                    ? selectedSourceOption?.textContent || "-"
                    : "-"
            );
            form.querySelectorAll("[data-planned-tank]").forEach((cell) => {
                const value = planned?.[cell.dataset.plannedTank];
                cell.textContent = Number.isFinite(value)
                    ? value.toFixed(1)
                    : "-";
            });
        };

        form.addEventListener("input", update);
        form.addEventListener("change", update);
        update();
    });
})();
