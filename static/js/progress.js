(function () {
  const bar = document.getElementById('top-progress');
  if (!bar || !window.htmx) {
    return;
  }

  function start() {
    bar.classList.remove('done');
    bar.classList.add('active');
  }

  function end() {
    bar.classList.add('done');
    setTimeout(() => {
      bar.classList.remove('active', 'done');
    }, 250);
  }

  document.body.addEventListener('htmx:beforeRequest', start);
  document.body.addEventListener('htmx:afterRequest', end);
  document.body.addEventListener('htmx:responseError', end);
  document.body.addEventListener('htmx:sendError', end);
  document.body.addEventListener('htmx:timeout', end);
})();
