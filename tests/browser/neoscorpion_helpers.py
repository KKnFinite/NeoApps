"""State-based waits for the production autosave UI, never direct database saves."""
def wait_for_fuel_saved(card, timeout=15000):
    # Autosave replaces its entire card. Re-resolve by persistent visit identity,
    # then wait until both the acknowledged baseline and displayed values agree.
    selector='[data-fuel-data-card][data-fuel-assignment-id="'+card.get_attribute('data-fuel-assignment-id')+'"]'
    work=card.get_attribute('data-physical-work-id')
    if work:
        selector+='[data-physical-work-id="'+work+'"]'
    card.page.wait_for_function("""selector => {
        const card=document.querySelector(selector), form=card?.querySelector('[data-fuel-autosave]');
        return card && form && card.dataset.fuelDataBusy!=='true'
            && window.NeoScorpionFuelData && !window.NeoScorpionFuelData.hasDirty(form)
            && !/failed|DATA CHANGED|Pending|Saving|draft/i.test(card.querySelector('[data-fuel-data-status]')?.textContent || '');
    }""",arg=selector,timeout=timeout)
