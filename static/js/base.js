(function () {
  function readCookie(name) {
    const key = `${name}=`;
    const parts = document.cookie.split(';');
    for (const part of parts) {
      const value = part.trim();
      if (value.startsWith(key)) {
        return decodeURIComponent(value.substring(key.length));
      }
    }
    return '';
  }

  function ensureFormCsrfToken(form) {
    const method = (form.getAttribute('method') || 'get').toLowerCase();
    if (method === 'get') {
      return;
    }

    const token = readCookie('csrf_token');
    if (!token) {
      return;
    }

    let field = form.querySelector('input[name="csrf_token"]');
    if (!field) {
      field = document.createElement('input');
      field.type = 'hidden';
      field.name = 'csrf_token';
      form.appendChild(field);
    }
    field.value = token;
  }

  function getToastContainer() {
    let container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      document.body.appendChild(container);
    }
    return container;
  }

  function toastPalette(type) {
    if (type === 'success') {
      return {
        card: 'border-emerald-200 bg-emerald-50 text-emerald-900',
        accent: 'bg-emerald-500',
        button: 'text-emerald-700 hover:bg-emerald-100',
      };
    }
    if (type === 'error') {
      return {
        card: 'border-red-200 bg-red-50 text-red-900',
        accent: 'bg-red-500',
        button: 'text-red-700 hover:bg-red-100',
      };
    }
    return {
      card: 'border-slate-200 bg-slate-50 text-slate-900',
      accent: 'bg-slate-500',
      button: 'text-slate-700 hover:bg-slate-100',
    };
  }

  function showToast(message, options = {}) {
    const text = String(message || '').trim();
    if (!text) {
      return;
    }

    const type = options.type || 'info';
    const duration = Number(options.duration || 4200);
    const palette = toastPalette(type);
    const container = getToastContainer();

    const toast = document.createElement('div');
    toast.className = `toast-enter rounded-xl border shadow-sm ${palette.card}`;

    const body = document.createElement('div');
    body.className = 'flex items-start justify-between gap-3 px-3 py-2.5 text-sm';

    const textNode = document.createElement('p');
    textNode.className = 'leading-5';
    textNode.textContent = text;

    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = `rounded-md px-2 py-0.5 text-xs font-semibold transition ${palette.button}`;
    closeBtn.textContent = 'Close';

    const progressTrack = document.createElement('div');
    progressTrack.className = 'h-1 w-full overflow-hidden rounded-b-xl bg-black/5';
    const progress = document.createElement('div');
    progress.className = `h-full ${palette.accent}`;
    progress.style.width = '100%';
    progress.style.transition = `width ${duration}ms linear`;
    progressTrack.appendChild(progress);

    body.appendChild(textNode);
    body.appendChild(closeBtn);
    toast.appendChild(body);
    toast.appendChild(progressTrack);
    container.appendChild(toast);

    requestAnimationFrame(() => {
      toast.classList.remove('toast-enter');
      toast.classList.add('toast-show');
      progress.style.width = '0%';
    });

    let closed = false;
    function closeToast() {
      if (closed) {
        return;
      }
      closed = true;
      toast.classList.remove('toast-show');
      toast.classList.add('toast-hide');
      setTimeout(() => toast.remove(), 170);
    }

    closeBtn.addEventListener('click', closeToast);
    setTimeout(closeToast, duration + 30);
  }

  function consumeToasts(root = document) {
    root.querySelectorAll('[data-toast-message]').forEach((node) => {
      const message = node.getAttribute('data-toast-message') || '';
      const type = node.getAttribute('data-toast-type') || 'info';
      const duration = Number(node.getAttribute('data-toast-duration') || '4200');
      showToast(message, { type, duration });
      node.remove();
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('form').forEach((form) => ensureFormCsrfToken(form));
    consumeToasts(document);
  });

  document.addEventListener('htmx:afterSwap', (event) => {
    const target = event.detail && event.detail.target;
    if (target) {
      target.querySelectorAll('form').forEach((form) => ensureFormCsrfToken(form));
      consumeToasts(target);
    }
  });

  document.addEventListener(
    'submit',
    (event) => {
      const form = event.target;
      if (form && form.tagName === 'FORM') {
        ensureFormCsrfToken(form);
      }
    },
    true,
  );

  document.addEventListener('htmx:configRequest', (event) => {
    const token = readCookie('csrf_token');
    if (token) {
      event.detail.headers['X-CSRF-Token'] = token;
    }
  });

  window.showToast = showToast;
})();
