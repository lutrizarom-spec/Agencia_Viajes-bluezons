// Punto de entrada: conecta la UI con la API, maneja sesión, filtros,
// detalle/reserva y "Mis reservas".

import * as api from "./api.js";
import {
  $,
  $$,
  clear,
  closeModal,
  formatClp,
  formatDateTime,
  openModal,
  setBusy,
  toast,
  trapFocus,
} from "./ui.js";
import { renderDetail, renderPackages, renderPriceChart, renderReservations } from "./render.js";

const state = {
  session: null,
  packages: [],
  currentPackage: null,
  filters: { type: "todos", sort: "codigo", search: "" },
};

const els = {
  loginPanel: $("#login-panel"),
  appPanel: $("#app-panel"),
  loginForm: $("#login-form"),
  loginError: $("#login-error"),
  loginButton: $("#login-button"),
  rut: $("#rut"),
  password: $("#password"),
  sessionRut: $("#session-rut"),
  sessionRole: $("#session-role"),
  fxRate: $("#fx-rate"),
  logout: $("#logout-button"),
  catalog: $("#catalog"),
  chart: $("#price-chart"),
  catalogError: $("#catalog-error"),
  catalogStatus: $("#catalog-status"),
  travelDate: $("#travel-date"),
  typeFilter: $("#type-filter"),
  sortFilter: $("#sort-filter"),
  searchFilter: $("#search-filter"),
  reload: $("#reload-button"),
  reservationsPanel: $("#reservations-panel"),
  reservations: $("#reservations"),
  reservationsStatus: $("#reservations-status"),
  loadReservations: $("#load-reservations"),
  modal: $("#detail-modal"),
  detailBody: $("#detail-body"),
  themeToggle: $("#theme-toggle"),
};

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("agencia-theme", theme);
  els.themeToggle.setAttribute("aria-pressed", String(theme === "dark"));
  els.themeToggle.textContent = theme === "dark" ? "Tema claro" : "Tema oscuro";
}

function initTheme() {
  const stored = localStorage.getItem("agencia-theme");
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(stored || (prefersDark ? "dark" : "light"));
}

function describeError(prefix, error) {
  return `${prefix}: ${error.message}`;
}

async function loadExchangeRate() {
  try {
    const data = await api.fetchExchangeRate();
    els.fxRate.textContent = `1 ${data.base_currency} = ${formatClp(data.rate)} ${data.target_currency}`;
    els.fxRate.classList.remove("muted");
  } catch (error) {
    els.fxRate.textContent = `Tipo de cambio no disponible (${error.message}).`;
    els.fxRate.classList.add("muted");
  }
}

function applyFilters(packages) {
  const { type, sort, search } = state.filters;
  let result = packages.slice();
  if (type !== "todos") {
    result = result.filter((p) => p.tipo === type);
  }
  if (search.trim()) {
    const needle = search.trim().toLowerCase();
    result = result.filter((p) => p.nombre.toLowerCase().includes(needle));
  }
  result.sort((a, b) => {
    switch (sort) {
      case "precio-asc":
        return a.precio_por_persona - b.precio_por_persona;
      case "precio-desc":
        return b.precio_por_persona - a.precio_por_persona;
      case "cupos":
        return (b.cupos_disponibles ?? -1) - (a.cupos_disponibles ?? -1);
      default:
        return a.codigo - b.codigo;
    }
  });
  return result;
}

function renderCatalog() {
  const visible = applyFilters(state.packages);
  renderPackages(els.catalog, visible, { onSelect: openDetail });
  renderPriceChart(els.chart, visible);
  const total = state.packages.length;
  els.catalogStatus.textContent = `${visible.length} de ${total} paquete(s) mostrados.`;
}

async function loadCatalog() {
  clear(els.catalogError);
  els.catalogError.hidden = true;
  els.catalogStatus.textContent = "Cargando catálogo…";
  try {
    state.packages = await api.fetchPackages(els.travelDate.value);
    renderCatalog();
  } catch (error) {
    els.catalogStatus.textContent = "";
    els.catalogError.textContent = describeError("Catálogo", error);
    els.catalogError.hidden = false;
    toast(describeError("Catálogo", error), "error");
  }
}

function openDetail(pkg) {
  state.currentPackage = pkg;
  renderDetail(els.detailBody, pkg, { onSubmit: handleReservation });
  openModal(els.modal);
}

function closeDetail() {
  closeModal(els.modal);
  state.currentPackage = null;
}

async function handleReservation(payload, button) {
  if (payload === null) {
    closeDetail();
    return;
  }
  setBusy(button, true);
  try {
    const receipt = await api.createReservation(payload);
    closeDetail();
    toast(`Reserva ${receipt.reservation_id.slice(0, 8)} creada (pago ${receipt.payment_status}).`, "success");
    await loadReservations();
  } catch (error) {
    toast(describeError("Reserva", error), "error");
    const err = els.detailBody.querySelector(".error");
    if (err) {
      err.textContent = error.message;
      err.hidden = false;
    }
  } finally {
    setBusy(button, false);
  }
}

async function loadReservations() {
  els.reservationsStatus.textContent = "Cargando reservas…";
  try {
    const reservations = await api.fetchReservations();
    renderReservations(els.reservations, reservations, { onCancel: handleCancel });
    els.reservationsStatus.textContent = `${reservations.length} reserva(s).`;
  } catch (error) {
    els.reservationsStatus.textContent = "";
    toast(describeError("Reservas", error), "error");
  }
}

async function handleCancel(reservation, button) {
  if (!window.confirm(`¿Cancelar la reserva del paquete ${reservation.package_code}?`)) {
    return;
  }
  setBusy(button, true);
  try {
    await api.cancelReservation(reservation.reservation_id);
    toast("Reserva cancelada.", "success");
    await loadReservations();
  } catch (error) {
    toast(describeError("Cancelar", error), "error");
  } finally {
    setBusy(button, false);
  }
}

function enterSession(session) {
  state.session = session;
  api.setToken(session.access_token);
  els.sessionRut.textContent = session.rut;
  els.sessionRole.textContent = session.role;
  els.loginPanel.hidden = true;
  els.appPanel.hidden = false;
  els.reservationsPanel.hidden = false;
  loadExchangeRate();
  loadCatalog();
  loadReservations();
}

function leaveSession() {
  state.session = null;
  api.setToken(null);
  state.packages = [];
  els.password.value = "";
  clear(els.catalog);
  clear(els.chart);
  clear(els.reservations);
  els.appPanel.hidden = true;
  els.reservationsPanel.hidden = true;
  els.loginPanel.hidden = false;
  els.catalogError.hidden = true;
  els.loginError.hidden = true;
  els.loginError.textContent = "";
}

async function handleLogin(event) {
  event.preventDefault();
  els.loginError.hidden = true;
  const rut = els.rut.value.trim();
  const password = els.password.value;
  if (!rut || !password) {
    els.loginError.textContent = "Ingresa RUT y clave.";
    els.loginError.hidden = false;
    return;
  }
  setBusy(els.loginButton, true);
  try {
    const session = await api.login(rut, password);
    enterSession(session);
    toast(`Bienvenido, ${session.rut}.`, "success");
  } catch (error) {
    els.loginError.textContent = error.message;
    els.loginError.hidden = false;
  } finally {
    setBusy(els.loginButton, false);
  }
}

function wireEvents() {
  els.loginForm.addEventListener("submit", handleLogin);
  els.logout.addEventListener("click", leaveSession);
  els.reload.addEventListener("click", loadCatalog);
  els.travelDate.addEventListener("change", loadCatalog);
  els.loadReservations.addEventListener("click", loadReservations);
  els.themeToggle.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
  els.typeFilter.addEventListener("change", () => {
    state.filters.type = els.typeFilter.value;
    renderCatalog();
  });
  els.sortFilter.addEventListener("change", () => {
    state.filters.sort = els.sortFilter.value;
    renderCatalog();
  });
  els.searchFilter.addEventListener("input", () => {
    state.filters.search = els.searchFilter.value;
    renderCatalog();
  });

  $$("[data-close-modal]", els.modal).forEach((node) => node.addEventListener("click", closeDetail));
  els.modal.addEventListener("keydown", (event) => trapFocus(els.modal, event));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.modal.hidden) {
      closeDetail();
    }
  });
}

initTheme();
wireEvents();
