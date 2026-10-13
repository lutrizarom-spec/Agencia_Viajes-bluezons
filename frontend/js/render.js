// Render de tarjetas, gráficos SVG y fichas de detalle. Usa textContent para
// los datos de la API (evita inyección) e innerHTML solo con íconos estáticos.

import { $, clear, formatClp, formatDate, formatDateTime, formatNumber } from "./ui.js";

const TYPE_META = {
  nacional: {
    label: "Nacional",
    className: "type--nacional",
    icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 20h18M6 20l2-9 4 3 2-6 2 6 4-3 2 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  },
  internacional: {
    label: "Internacional",
    className: "type--internacional",
    icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12l20-8-8 20-2.5-8.5L2 12z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  },
  crucero: {
    label: "Crucero",
    className: "type--crucero",
    icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 15l2-6h12l2 6M12 9V4M3 18c2 1.2 4 1.2 6 0s4-1.2 6 0 4 1.2 6 0" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  },
  turistico: {
    label: "Genérico",
    className: "type--generico",
    icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="7" width="16" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  },
};

function metaFor(tipo) {
  return TYPE_META[tipo] || TYPE_META.turistico;
}

function tag(text, className) {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  return span;
}

export function typeBadge(tipo) {
  const meta = metaFor(tipo);
  const wrapper = document.createElement("span");
  wrapper.className = `type-badge ${meta.className}`;
  wrapper.innerHTML = meta.icon;
  wrapper.appendChild(tag(meta.label, "type-badge__label"));
  return wrapper;
}

function availability(disponibles) {
  const wrapper = document.createElement("div");
  wrapper.className = "availability";
  if (disponibles === null || disponibles === undefined) {
    wrapper.appendChild(tag("Cupos sin configurar", "availability__label muted"));
    return wrapper;
  }
  const capped = Math.min(Math.max(disponibles, 0), 30);
  const bar = document.createElement("div");
  bar.className = "availability__bar";
  const fill = document.createElement("span");
  fill.className = "availability__fill";
  fill.style.width = `${(capped / 30) * 100}%`;
  fill.style.background = disponibles === 0 ? "var(--danger)" : disponibles <= 3 ? "var(--warn)" : "var(--ok)";
  bar.appendChild(fill);
  const label = document.createElement("span");
  label.className = "availability__label";
  label.textContent = disponibles === 0 ? "Sin cupos" : `${disponibles} cupos`;
  wrapper.append(bar, label);
  return wrapper;
}

export function renderPackages(container, packages, { onSelect } = {}) {
  clear(container);
  const grid = document.createElement("div");
  grid.className = "cards";

  if (!packages.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No hay paquetes para los filtros seleccionados.";
    grid.appendChild(empty);
    container.appendChild(grid);
    return;
  }

  for (const pkg of packages) {
    const card = document.createElement("article");
    card.className = "card-pkg";
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `Ver detalle de ${pkg.nombre}`);
    card.dataset.code = String(pkg.codigo);

    const head = document.createElement("div");
    head.className = "card-pkg__head";
    head.appendChild(typeBadge(pkg.tipo));

    const name = document.createElement("h3");
    name.className = "card-pkg__name";
    name.textContent = pkg.nombre;

    const duration = document.createElement("p");
    duration.className = "card-pkg__duration";
    duration.textContent = `${pkg.duracion} días`;

    const price = document.createElement("p");
    price.className = "card-pkg__price";
    price.textContent = formatClp(pkg.precio_por_persona);

    const code = document.createElement("span");
    code.className = "card-pkg__code";
    code.textContent = `Código ${pkg.codigo}`;

    card.append(head, name, duration, availability(pkg.cupos_disponibles), price, code);
    card.addEventListener("click", () => onSelect && onSelect(pkg));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onSelect && onSelect(pkg);
      }
    });
    grid.appendChild(card);
  }
  container.appendChild(grid);
}

export function renderPriceChart(container, packages) {
  clear(container);
  if (!packages.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Sin datos para graficar.";
    container.appendChild(empty);
    return;
  }

  const width = 520;
  const barHeight = 26;
  const gap = 10;
  const labelWidth = 150;
  const height = packages.length * (barHeight + gap) + 10;
  const max = Math.max(...packages.map((p) => Number(p.precio_por_persona) || 0), 1);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Comparación de precios por persona por paquete");

  packages.forEach((pkg, index) => {
    const y = index * (barHeight + gap) + 5;
    const value = Number(pkg.precio_por_persona) || 0;
    const barWidth = ((width - labelWidth - 90) * value) / max;

    const name = document.createElementNS("http://www.w3.org/2000/svg", "text");
    name.setAttribute("x", "0");
    name.setAttribute("y", String(y + barHeight / 2 + 4));
    name.setAttribute("class", "chart__label");
    name.textContent = `${pkg.codigo} · ${metaFor(pkg.tipo).label}`;

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(labelWidth));
    rect.setAttribute("y", String(y));
    rect.setAttribute("width", String(Math.max(barWidth, 2)));
    rect.setAttribute("height", String(barHeight));
    rect.setAttribute("rx", "4");
    rect.setAttribute("class", `chart__bar ${metaFor(pkg.tipo).className}`);

    const amount = document.createElementNS("http://www.w3.org/2000/svg", "text");
    amount.setAttribute("x", String(labelWidth + Math.max(barWidth, 2) + 6));
    amount.setAttribute("y", String(y + barHeight / 2 + 4));
    amount.setAttribute("class", "chart__value");
    amount.textContent = formatClp(value);

    svg.append(name, rect, amount);
  });
  container.appendChild(svg);
}

function field(labelText, input) {
  const wrapper = document.createElement("label");
  wrapper.className = "field";
  const span = document.createElement("span");
  span.className = "field__label";
  span.textContent = labelText;
  wrapper.append(span, input);
  return wrapper;
}

export function renderDetail(container, pkg, { onSubmit } = {}) {
  clear(container);

  const header = document.createElement("div");
  header.className = "detail__header";
  header.appendChild(typeBadge(pkg.tipo));
  const title = document.createElement("h2");
  title.id = "detail-title";
  title.textContent = pkg.nombre;
  const code = document.createElement("p");
  code.className = "muted";
  code.textContent = `Código ${pkg.codigo} · ${pkg.duracion} días`;
  header.append(title, code);

  const priceRow = document.createElement("p");
  priceRow.className = "detail__price";
  priceRow.textContent = `${formatClp(pkg.precio_por_persona)} por persona`;

  const extras = document.createElement("ul");
  extras.className = "detail__extras";
  if (pkg.tipo === "internacional") {
    const li = document.createElement("li");
    li.textContent = "Requiere pasaporte registrado del titular.";
    extras.appendChild(li);
  }
  if (pkg.tipo === "crucero") {
    const li = document.createElement("li");
    li.textContent = "Incluye impuesto de puerto en el precio calculado.";
    extras.appendChild(li);
  }

  const form = document.createElement("form");
  form.className = "detail__form";
  form.noValidate = true;

  const quantity = document.createElement("input");
  quantity.type = "number";
  quantity.min = "1";
  quantity.step = "1";
  quantity.value = "1";
  quantity.required = true;
  quantity.id = "reserve-quantity";

  const travelDate = document.createElement("input");
  travelDate.type = "date";
  travelDate.required = true;
  travelDate.id = "reserve-date";
  travelDate.dataset.autofocus = "true";

  const email = document.createElement("input");
  email.type = "email";
  email.placeholder = "opcional";
  email.id = "reserve-email";

  form.append(
    field("Cantidad de cupos", quantity),
    field("Fecha de viaje", travelDate),
    field("Correo de notificación", email),
  );

  let passport = null;
  if (pkg.tipo === "internacional") {
    passport = document.createElement("input");
    passport.type = "text";
    passport.maxLength = 32;
    passport.required = true;
    passport.placeholder = "AB123456";
    passport.id = "reserve-passport";
    form.appendChild(field("Pasaporte del titular", passport));
  }

  const error = document.createElement("p");
  error.className = "error";
  error.setAttribute("role", "alert");
  error.hidden = true;

  const actions = document.createElement("div");
  actions.className = "detail__actions";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.textContent = "Reservar";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "secondary";
  cancel.textContent = "Cerrar";
  cancel.addEventListener("click", () => onSubmit && onSubmit(null));
  actions.append(submit, cancel);

  form.append(error, actions);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    error.hidden = true;
    const payload = {
      package_code: pkg.codigo,
      quantity: Number(quantity.value),
      travel_date: travelDate.value,
    };
    if (email.value.trim()) {
      payload.notification_email = email.value.trim();
    }
    if (passport) {
      payload.passport = passport.value.trim();
    }
    if (!payload.quantity || payload.quantity < 1 || !payload.travel_date) {
      error.textContent = "Completa cantidad y fecha válidas.";
      error.hidden = false;
      return;
    }
    onSubmit && onSubmit(payload, submit);
  });

  container.append(header, priceRow, extras, form);
}

const STATUS_META = {
  confirmed: { label: "Confirmada", className: "status--ok" },
  confirmada: { label: "Confirmada", className: "status--ok" },
  confirmado: { label: "Confirmado", className: "status--ok" },
  cancelled: { label: "Cancelada", className: "status--danger" },
  cancelada: { label: "Cancelada", className: "status--danger" },
  pending: { label: "Pendiente", className: "status--warn" },
  pendiente: { label: "Pendiente", className: "status--warn" },
  failed: { label: "Fallido", className: "status--danger" },
  fallido: { label: "Fallido", className: "status--danger" },
};

export function statusLabel(status) {
  return STATUS_META[status] ? STATUS_META[status].label : status;
}

function statusTag(status) {
  const meta = STATUS_META[status] || { label: status, className: "" };
  const span = document.createElement("span");
  span.className = `status ${meta.className}`;
  span.textContent = meta.label;
  return span;
}

export function renderReservations(container, reservations, { onCancel } = {}) {
  clear(container);
  if (!reservations.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Todavía no tienes reservas. Crea una desde el catálogo.";
    container.appendChild(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "reservations";

  for (const r of reservations) {
    const item = document.createElement("article");
    item.className = "reservation";

    const top = document.createElement("div");
    top.className = "reservation__top";
    const title = document.createElement("strong");
    title.textContent = `Paquete ${r.package_code} · ${r.quantity} cupo(s)`;
    top.append(title, statusTag(r.status));

    const meta = document.createElement("p");
    meta.className = "reservation__meta";
    meta.textContent = `Viaje ${formatDate(r.travel_date)} · Total ${formatClp(r.total_price)} · Pagado ${formatClp(r.total_paid)} · Saldo ${formatClp(r.balance_due)}`;

    const pay = document.createElement("p");
    pay.className = "reservation__meta";
    pay.textContent = `Pago: ${statusLabel(r.payment_status)}${r.payment_expires_at ? ` (vence ${formatDateTime(r.payment_expires_at)})` : ""}`;

    const actions = document.createElement("div");
    actions.className = "reservation__actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary";
    cancel.textContent = "Cancelar";
    cancel.disabled = !["confirmed", "confirmada"].includes(r.status);
    cancel.addEventListener("click", () => onCancel && onCancel(r, cancel));
    actions.appendChild(cancel);

    item.append(top, meta, pay, actions);
    list.appendChild(item);
  }
  container.appendChild(list);
}

export { formatNumber };
