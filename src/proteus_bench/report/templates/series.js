// Switch the trend charts between run order and PROTEUS commit order. Charts drawn
// while hidden have no width, so the ones that appear are resized.
(() => {
  const order = document.getElementById('order');
  if (!order) return;
  order.addEventListener('change', () => {
    const byCommit = order.querySelector('input:checked').value === 'commit';
    for (const el of document.querySelectorAll('.by-run, .by-commit')) {
      el.hidden = el.classList.contains('by-commit') !== byCommit;
      if (!el.hidden) el.querySelectorAll('.js-plotly-plot').forEach(plot => Plotly.Plots.resize(plot));
    }
  });
})();

// Filter a series page's run table (and dim chart points of other runs) by settings key=value.
// Reads the index embedded as #settings-index: {key: {jsonValue: [run ids]}}.
(() => {
  const source = document.getElementById('settings-index');
  if (!source) return;
  const index = JSON.parse(source.textContent);
  const keySel = document.getElementById('f-key');
  const valSel = document.getElementById('f-value');
  const add = document.getElementById('f-add');
  const chips = document.getElementById('f-active');
  const status = document.getElementById('f-status');
  const rows = [...document.querySelectorAll('tr[data-run]')];
  const active = [];

  keySel.addEventListener('change', () => {
    valSel.replaceChildren();
    const values = index[keySel.value] || {};
    // numeric-aware, so values such as 2, 10 and 16 sort as numbers
    const sorted = Object.keys(values).sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
    for (const value of sorted) {
      valSel.append(new Option(`${value} (${values[value].length} runs)`, value));
    }
    valSel.disabled = add.disabled = !keySel.value;
  });

  add.addEventListener('click', () => {
    const key = keySel.value, value = valSel.value;
    if (!key || active.some(f => f.key === key && f.value === value)) return;
    active.push({key, value});
    apply();
  });

  // Plotly greys out the points a trace does not list in selectedpoints; null selects all
  function dim(allowed) {
    for (const el of document.querySelectorAll('.js-plotly-plot')) {
      el.data.forEach((trace, i) => {
        if (!trace.ids) return;
        const selected = allowed && trace.ids.flatMap((id, k) => (allowed.has(id) ? [k] : []));
        Plotly.restyle(el, {selectedpoints: [selected]}, [i]);
      });
    }
  }
  let allowed = null;
  document.addEventListener('charts-drawn', () => allowed && dim(allowed));

  function apply() {
    allowed = null;
    for (const {key, value} of active) {
      const ids = new Set(index[key][value]);
      allowed = allowed === null ? ids : new Set([...allowed].filter(id => ids.has(id)));
    }
    const shown = id => allowed === null || allowed.has(id);
    rows.forEach(tr => { tr.hidden = !shown(tr.dataset.run); });
    dim(allowed);
    chips.replaceChildren(...active.map((f, i) => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = `${f.key} = ${f.value} ✕`;
      btn.setAttribute('aria-label', `Remove filter ${f.key} = ${f.value}`);
      btn.addEventListener('click', () => { active.splice(i, 1); apply(); });
      li.append(btn);
      return li;
    }));
    const n = rows.filter(tr => !tr.hidden).length;
    status.textContent = active.length
      ? `${n} of ${rows.length} runs match; chart points of other runs are dimmed.`
      : `${Object.keys(index).length} settings differ between runs.`;
  }
})();
