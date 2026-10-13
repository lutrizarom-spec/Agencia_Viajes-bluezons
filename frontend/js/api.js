// Capa de acceso a la API. nginx reenvía estas rutas al backend, por lo que se
// usan rutas relativas y el frontend funciona en el mismo origen (sin CORS).

const API_BASE = "";

let accessToken = null;

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function setToken(token) {
  accessToken = token;
}

async function request(path, { method = "GET", body, headers = {} } = {}) {
  const finalHeaders = { ...headers };
  if (body !== undefined) {
    finalHeaders["Content-Type"] = "application/json";
  }
  if (accessToken) {
    finalHeaders["Authorization"] = `Bearer ${accessToken}`;
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: finalHeaders,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("No se pudo conectar con la API.", 0);
  }

  if (!response.ok) {
    throw new ApiError(await describeFailure(response), response.status);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

async function describeFailure(response) {
  try {
    const payload = await response.json();
    const detail = payload && payload.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail.map((item) => item.msg || "dato inválido").join(", ");
    }
  } catch {
    // La respuesta no era JSON; se usa el mensaje genérico.
  }
  return `Error ${response.status}`;
}

function newIdempotencyKey() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `key-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function login(rut, password) {
  return request("/auth/login", { method: "POST", body: { rut, password } });
}

export function fetchPackages(travelDate) {
  const query = travelDate ? `?travel_date=${encodeURIComponent(travelDate)}` : "";
  return request(`/paquetes${query}`);
}

export function fetchExchangeRate() {
  return request("/tipo-cambio");
}

export function createReservation(payload) {
  return request("/reservas", {
    method: "POST",
    body: payload,
    headers: { "X-Idempotency-Key": newIdempotencyKey() },
  });
}

export function fetchReservations() {
  return request("/reservas");
}

export function cancelReservation(reservationId) {
  return request(`/reservas/${encodeURIComponent(reservationId)}/cancelar`, { method: "POST" });
}

export function fetchPayments(reservationId) {
  return request(`/reservas/${encodeURIComponent(reservationId)}/pagos`);
}
