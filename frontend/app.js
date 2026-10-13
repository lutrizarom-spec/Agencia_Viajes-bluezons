"use strict";

// nginx reenvía /auth, /paquetes y /tipo-cambio a la API, así que el frontend
// usa rutas relativas y no necesita configurar CORS ni un host concreto.
const API_BASE = "";

const element = (id) => document.getElementById(id);

const clpFormatter = new Intl.NumberFormat("es-CL", {
  style: "currency",
  currency: "CLP",
  maximumFractionDigits: 0,
});

let accessToken = null;

function showError(target, message) {
  target.textContent = message;
  target.hidden = false;
}

function clearError(target) {
  target.textContent = "";
  target.hidden = true;
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) {
    headers["Content-Type"] = "application/json";
  }
  if (accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch (networkError) {
    throw new Error("No se pudo conectar con la API.");
  }

  if (!response.ok) {
    throw new Error(await describeFailure(response));
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

async function describeFailure(response) {
  try {
    const payload = await response.json();
    if (payload && typeof payload.detail === "string") {
      return payload.detail;
    }
    if (Array.isArray(payload && payload.detail)) {
      return payload.detail.map((item) => item.msg || "dato inválido").join(", ");
    }
  } catch (ignored) {
    // La respuesta no era JSON; se usa el mensaje genérico.
  }
  return `Error ${response.status}`;
}

function appendCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.appendChild(cell);
}

function renderPackages(packages) {
  const body = element("catalog-body");
  body.replaceChildren();

  if (!packages.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "empty";
    cell.textContent = "No hay paquetes para mostrar.";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }

  for (const item of packages) {
    const row = document.createElement("tr");
    appendCell(row, String(item.codigo));
    appendCell(row, item.nombre);
    appendCell(row, `${item.duracion} días`);
    appendCell(row, item.tipo);
    appendCell(row, clpFormatter.format(item.precio_por_persona));
    appendCell(
      row,
      item.cupos_disponibles === null ? "Sin configurar" : String(item.cupos_disponibles),
    );
    body.appendChild(row);
  }
}

async function loadCatalog() {
  clearError(element("catalog-error"));
  const travelDate = element("travel-date").value;
  const query = travelDate ? `?travel_date=${encodeURIComponent(travelDate)}` : "";
  try {
    const packages = await request(`/paquetes${query}`);
    renderPackages(packages);
  } catch (error) {
    showError(element("catalog-error"), `Catálogo: ${error.message}`);
  }
}

async function loadExchangeRate() {
  const target = element("fx-rate");
  try {
    const data = await request("/tipo-cambio");
    const rate = Number(data.rate).toLocaleString("es-CL", { maximumFractionDigits: 2 });
    target.textContent = `Tipo de cambio: 1 ${data.base_currency} = ${rate} ${data.target_currency}`;
  } catch (error) {
    target.textContent = `Tipo de cambio: no disponible (${error.message}).`;
  }
}

function enterSession(session) {
  accessToken = session.access_token;
  element("session-rut").textContent = session.rut;
  element("session-role").textContent = session.role;
  element("login-panel").hidden = true;
  element("catalog-panel").hidden = false;
  loadCatalog();
  loadExchangeRate();
}

function leaveSession() {
  accessToken = null;
  element("password").value = "";
  element("catalog-body").replaceChildren();
  element("catalog-panel").hidden = true;
  element("login-panel").hidden = false;
  clearError(element("login-error"));
  clearError(element("catalog-error"));
}

async function handleLogin(event) {
  event.preventDefault();
  clearError(element("login-error"));

  const rut = element("rut").value.trim();
  const password = element("password").value;
  if (!rut || !password) {
    showError(element("login-error"), "Ingresa RUT y clave.");
    return;
  }

  const button = element("login-button");
  button.disabled = true;
  try {
    const session = await request("/auth/login", {
      method: "POST",
      body: JSON.stringify({ rut, password }),
    });
    enterSession(session);
  } catch (error) {
    showError(element("login-error"), error.message);
  } finally {
    button.disabled = false;
  }
}

element("login-form").addEventListener("submit", handleLogin);
element("logout-button").addEventListener("click", leaveSession);
element("reload-button").addEventListener("click", loadCatalog);
