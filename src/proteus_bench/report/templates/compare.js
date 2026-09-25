// Two-run comparison. Runs come from #compare-data ({runs: [{id, label, totals, ...}]},
// newest first); the pair from ?a=<run_id>&b=<run_id>, defaulting to the two newest.
(() => {
  const data = JSON.parse(document.getElementById('compare-data').textContent);
  const byId = new Map(data.runs.map(r => [r.id, r]));
  const selA = document.getElementById('cmp-a');
  const selB = document.getElementById('cmp-b');
  const out = document.getElementById('cmp-out');
  const status = document.getElementById('cmp-status');
  const params = new URLSearchParams(location.search);

  if (data.runs.length < 2) {
    status.textContent = 'At least two runs are needed for a comparison.';
    return;
  }
  for (const sel of [selA, selB]) {
    data.runs.forEach(r => sel.append(new Option(r.label, r.id)));
  }
  const missing = ['a', 'b'].filter(k => params.has(k) && !byId.has(params.get(k)));
  selA.value = byId.has(params.get('a')) ? params.get('a') : data.runs[1].id;
  selB.value = byId.has(params.get('b')) ? params.get('b') : data.runs[0].id;

  const fmt = s => {
    if (s === undefined) return 'n/a';
    const a = Math.abs(s), sign = s < 0 ? '-' : '';
    if (a < 100) return `${s.toFixed(1)} s`;
    if (a < 3600) { const r = Math.round(a); return `${sign}${Math.floor(r / 60)}:${String(r % 60).padStart(2, '0')} min`; }
    const m = Math.round(a / 60);
    return `${sign}${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
  };
  const cell = (text, cls) => { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; return td; };
  const runLink = r => { const a = document.createElement('a'); a.href = `runs/${r.id}.html`; a.textContent = r.label; return a; };

  function deltaCell(rel) {
    const td = document.createElement('td');
    const track = document.createElement('div');
    track.className = 'delta';
    if (Number.isFinite(rel) && rel !== 0) {
      const bar = document.createElement('span');
      bar.className = rel > 0 ? 'bad' : 'good';
      bar.style.width = `${Math.min(Math.abs(rel), 0.5) * 100}%`;  // full half-width at 50 %
      track.append(bar);
    }
    td.append(track);
    return td;
  }

  function render() {
    const a = byId.get(selA.value), b = byId.get(selB.value);
    history.replaceState(null, '', `?a=${encodeURIComponent(a.id)}&b=${encodeURIComponent(b.id)}`);
    const notes = missing.map(k => `Run ${params.get(k)} (${k}) is not in this site.`);
    if (a.id === b.id) notes.push('Run A and run B are the same run.');
    if (a.n_iters !== b.n_iters) notes.push(`Iteration counts differ: ${a.n_iters} vs ${b.n_iters}.`);
    if (!a.comparable || !b.comparable) notes.push('At least one run is not comparable (see its run page).');
    status.textContent = notes.join(' ');
    const names = [...new Set([...Object.keys(a.totals), ...Object.keys(b.totals)])];
    names.sort((x, y) => Math.max(b.totals[y] || 0, a.totals[y] || 0) - Math.max(b.totals[x] || 0, a.totals[x] || 0));
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th>Quantity</th><th class="num">A</th><th class="num">B</th>'
      + '<th class="num">B - A</th><th class="num">Change</th><th>Slower / faster</th></tr></thead>';
    const body = document.createElement('tbody');
    for (const name of names) {
      const va = a.totals[name], vb = b.totals[name];
      const both = va !== undefined && vb !== undefined;
      const rel = both && va > 0 ? vb / va - 1 : NaN;
      const tr = document.createElement('tr');
      tr.append(cell(name, 'mono'), cell(fmt(va), 'num'), cell(fmt(vb), 'num'),
        cell(both ? fmt(vb - va) : 'n/a', 'num'),
        cell(Number.isFinite(rel) ? `${rel >= 0 ? '+' : ''}${(rel * 100).toFixed(1)} %` : 'n/a', 'num'),
        deltaCell(rel));
      body.append(tr);
    }
    table.append(body);
    const heading = document.createElement('p');
    heading.append('A: ', runLink(a), document.createElement('br'), 'B: ', runLink(b));
    const panel = document.createElement('div');
    panel.className = 'panel scroll';
    panel.append(table);
    out.replaceChildren(heading, panel);
  }

  selA.addEventListener('change', render);
  selB.addEventListener('change', render);
  document.getElementById('cmp-swap').addEventListener('click', () => {
    [selA.value, selB.value] = [selB.value, selA.value];
    render();
  });
  render();
})();
