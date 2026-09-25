// Filter a series page's run table (and dim chart points) by settings key=value.
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
  const points = [...document.querySelectorAll('.pt[data-run]')];
  const active = [];

  keySel.addEventListener('change', () => {
    valSel.replaceChildren();
    const values = index[keySel.value] || {};
    for (const value of Object.keys(values).sort()) {
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

  function apply() {
    let allowed = null;
    for (const {key, value} of active) {
      const ids = new Set(index[key][value]);
      allowed = allowed === null ? ids : new Set([...allowed].filter(id => ids.has(id)));
    }
    const shown = id => allowed === null || allowed.has(id);
    rows.forEach(tr => { tr.hidden = !shown(tr.dataset.run); });
    points.forEach(g => g.classList.toggle('dim', !shown(g.dataset.run)));
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
