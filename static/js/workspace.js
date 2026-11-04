/* Navigation filtering only touches the links already permitted by the server. */
(function () {
  const nav = document.querySelector('#sidebar nav');
  if (!nav) return;
  const field = document.getElementById('nav-filter');
  const links = Array.from(nav.querySelectorAll('.nav-link'));
  const labels = Array.from(nav.querySelectorAll('.nav-section-label'));
  const empty = document.createElement('p');
  empty.className = 'nav-empty';
  empty.textContent = 'No matching sections.';
  empty.hidden = true;
  nav.appendChild(empty);
  field.addEventListener('input', function () {
    const query = field.value.trim().toLowerCase();
    links.forEach(link => { link.hidden = !link.textContent.toLowerCase().includes(query); link.style.display = link.hidden ? 'none' : ''; });
    labels.forEach(label => { label.hidden = Boolean(query); });
    empty.hidden = links.some(link => !link.hidden);
  });
  field.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') { field.value = ''; field.dispatchEvent(new Event('input')); }
  });
  const current = nav.querySelector('.nav-link.active');
  if (current) { current.setAttribute('aria-current', 'page'); current.scrollIntoView({block: 'nearest'}); }
  // Make all data grids scroll within their panel on narrow screens.
  document.querySelectorAll('main table').forEach(table => {
    if (table.closest('.overflow-x-auto, .overflow-auto')) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'overflow-x-auto';
    wrapper.tabIndex = 0;
    wrapper.setAttribute('role', 'region');
    wrapper.setAttribute('aria-label', 'Scrollable data table');
    table.parentNode.insertBefore(wrapper, table);
    wrapper.appendChild(table);
  });
})();
