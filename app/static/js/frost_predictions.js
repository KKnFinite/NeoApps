(() => {
    'use strict';
    const root = document.querySelector('[data-frost-predictions]');
    if (!root) return;
    const feedback = root.querySelector('[data-prediction-feedback]');
    const content = root.querySelector('[data-prediction-content]');
    const text = (tag, value, parent) => {
        const element = document.createElement(tag); element.textContent = value; parent.append(element); return element;
    };
    const shown = value => value === null || value === undefined ? 'Unknown' : String(value);
    let busy = false;
    root.addEventListener('click', async event => {
        const button = event.target.closest('[data-prediction-url]');
        if (!button || busy) return;
        busy = true; button.disabled = true; feedback.textContent = 'Reading verified private evidence…';
        const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 20000);
        try {
            const response = await fetch(button.dataset.predictionUrl, {credentials:'same-origin', cache:'no-store', signal:controller.signal});
            if (!response.ok) throw new Error(`Evidence unavailable (${response.status}). Try again; previous view retained.`);
            const value = await response.json();
            if (!Array.isArray(value.predictions) || value.predictions.length > 8 || value.approved !== false) throw new Error('Unexpected research evidence.');
            const fragment = document.createDocumentFragment();
            const source = value.source || {};
            text('p', `Capture ${value.captured_at_utc} · Committed ${value.committed_at_utc} · UTC`, fragment);
            const observations = (source.observations || []).filter(row => Date.parse(row.utc) <= Date.parse(source.cutoff_at_utc)).sort((a,b) => Date.parse(b.utc)-Date.parse(a.utc));
            const obs = observations[0] || {};
            text('p', `KRFD ${shown(obs.utc)} · ${shown(obs.temperature_c)} °C · dew point ${shown(obs.dewpoint_c)} °C · wind ${shown(obs.wind_kt)} kt · HRRR cycle ${shown(source.native_point?.cycle_at_utc)}`, fragment);
            text('p', obs.raw_metar || 'Original METAR unavailable', fragment);
            const grid = document.createElement('div'); grid.className = 'frost-prediction-grid'; fragment.append(grid);
            for (const h of [0,1,3,6]) {
                const card = text('article', '', grid);
                text('h3', `H${h}${h === 0 ? ' · CURRENT RECOGNITION' : ' · RESEARCH'}`, card);
                for (const prediction of value.predictions.filter(p => p.hours === h)) {
                    const kind = /logistic/i.test(prediction.model_version) ? 'Logistic' : 'Forest';
                    const score = prediction.state === 'SCORED' && Number.isFinite(prediction.score) ? prediction.score.toFixed(4) : 'UNAVAILABLE';
                    text('p', `${kind}: ${score} · Target ${prediction.target_at_utc}`, card);
                    text('p', prediction.reason || `Fixed threshold ${prediction.threshold} · ${prediction.classification ? 'FROST-reason class' : 'Other-treatment class'}`, card);
                    const details = text('details', '', card); text('summary', `${kind} model & input quality`, details);
                    text('p', prediction.model_version, details); text('code', prediction.model_sha256, details);
                    text('p', `Missing features: ${JSON.stringify(prediction.vector?.missing_features || prediction.vector?.missing || [])}`, details);
                    text('p', `Input quality: ${JSON.stringify(prediction.vector?.quality || [])}`, details);
                    for (const forecast of prediction.vector?.provenance?.forecasts || []) {
                        text('p', `Forecast valid ${shown(forecast.valid_at_utc)} · Available by ${shown(forecast.available_by_utc)}`, details);
                    }
                }
            }
            content.replaceChildren(fragment); feedback.textContent = 'Private evidence verified. Outcomes remain unknown unless separately linked.';
        } catch (error) { feedback.textContent = error.name === 'AbortError' ? 'Evidence request timed out. Try again; previous view retained.' : error.message; }
        finally { clearTimeout(timer); busy = false; button.disabled = false; }
    });
})();
