// Draws each chart (a script.figure holding {light, dark} Plotly figures, then its
// figure.plot) with the template of the reader's colour scheme, again when it changes.
// Clicking a point with a run-page path in its customdata opens that page.
(() => {
  const light = matchMedia('(prefers-color-scheme: light)');
  const plots = [...document.querySelectorAll('script.figure')].map(s => ({
    el: s.nextElementSibling,
    figure: JSON.parse(s.textContent),
  }));
  const templates = JSON.parse(document.getElementById('plotly-templates').textContent);
  const config = {displayModeBar: false, responsive: true};

  async function draw() {
    const theme = light.matches ? 'light' : 'dark';
    await Promise.all(plots.map(({el, figure}) => Plotly.react(el, figure[theme].data, {...figure[theme].layout, template: templates[theme]}, config)));
    document.dispatchEvent(new Event('charts-drawn'));
  }

  function openRunOnClick() {
    for (const {el} of plots) {
      el.on('plotly_click', e => {
        const href = e.points[0].customdata;
        if (typeof href === 'string') location.href = href;
      });
    }
  }

  draw().then(openRunOnClick, console.error);
  light.addEventListener('change', () => draw().catch(console.error));
})();
