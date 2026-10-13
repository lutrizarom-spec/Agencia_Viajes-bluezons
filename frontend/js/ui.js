// Utilidades de interfaz: selección de nodos, formato, toasts, estado de carga
// y control del modal. Sin dependencias externas.

export const $ = (selector, scope = document) => scope.querySelector(selector);
export const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

const clpFormatter = new Intl.NumberFormat("es-CL", {
  style: "currency",
  currency: "CLP",
  maximumFractionDigits: 0,
});

const numberFormatter = new Intl.NumberFormat("es-CL", { maximumFractionDigits: 2 });

export function formatClp(value) {
  return clpFormatter.format(Number(value) || 0);
}

export function formatNumber(value) {
  return numberFormatter.format(Number(value) || 0);
}

export function formatDate(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleDateString("es-CL", { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString("es-CL", { dateStyle: "medium", timeStyle: "short" });
}

export function setBusy(button, busy) {
  if (!button) {
    return;
  }
  button.disabled = busy;
  button.classList.toggle("is-loading", busy);
  button.setAttribute("aria-busy", String(busy));
}

export function toast(message, type = "info") {
  const container = $("#toast-container");
  if (!container) {
    return;
  }
  const node = document.createElement("div");
  node.className = `toast toast--${type}`;
  node.setAttribute("role", "status");
  node.textContent = message;
  container.appendChild(node);
  window.setTimeout(() => node.classList.add("toast--visible"), 10);
  window.setTimeout(() => {
    node.classList.remove("toast--visible");
    window.setTimeout(() => node.remove(), 300);
  }, 4200);
}

export function openModal(modal) {
  modal.hidden = false;
  document.body.classList.add("modal-open");
  const focusable = modal.querySelector("[data-autofocus]") || modal.querySelector("button, input, select");
  if (focusable) {
    focusable.focus();
  }
}

export function closeModal(modal) {
  modal.hidden = true;
  document.body.classList.remove("modal-open");
}

export function trapFocus(modal, event) {
  if (event.key !== "Tab") {
    return;
  }
  const focusable = $$(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    modal,
  ).filter((node) => !node.disabled && node.offsetParent !== null);
  if (focusable.length === 0) {
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

export function clear(node) {
  while (node && node.firstChild) {
    node.removeChild(node.firstChild);
  }
}
