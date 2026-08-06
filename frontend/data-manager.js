(function () {
    const STORAGE_KEY = 'rh_empleados_v1';
    const UPDATE_KEY = 'rh_last_update_v1';

    function normalizeText(value) {
        return String(value || '').trim();
    }

    function normalizeCurp(value) {
        return normalizeText(value).toUpperCase();
    }

    function normalizeStatus(value) {
        return normalizeText(value).toUpperCase();
    }

    function parseFechaValor(value) {
        if (!value) return 0;
        const parsed = Date.parse(String(value));
        return Number.isNaN(parsed) ? 0 : parsed;
    }

    function resolveStatus(existingStatus, incomingStatus, existingOverride = false) {
        const existing = normalizeStatus(existingStatus || '');
        const incoming = normalizeStatus(incomingStatus || 'ACTIVO');

        if (existingOverride && existing) {
            return existing;
        }

        if (existing) {
            return existing;
        }

        return incoming;
    }

    function formatCommentText(texto) {
        const normalized = normalizeText(texto);
        if (!normalized) return '';

        const alreadyPrefixed = /^\[[^\]]+\]\s*-\s*/.test(normalized);
        if (alreadyPrefixed) {
            return normalized;
        }

        return `[${formatDateTime(new Date())}] - ${normalized}`;
    }

    function formatDateOnly(dateObj) {
        const date = dateObj instanceof Date ? dateObj : new Date(dateObj);
        if (!date || Number.isNaN(date.getTime())) return '';
        const day = String(date.getDate()).padStart(2, '0');
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const year = String(date.getFullYear());
        return `${day}/${month}/${year}`;
    }

    function getCommentParts(commentText) {
        const normalized = normalizeText(commentText);
        const match = normalized.match(/^\[([^\]]+)\]\s*-\s*(.*)$/);
        if (match) {
            const rawDate = normalizeText(match[1]);
            const dateOnlyMatch = rawDate.match(/\d{2}\/\d{2}\/\d{4}/);
            const dateOnly = dateOnlyMatch ? dateOnlyMatch[0] : rawDate;
            return { date: dateOnly, body: normalizeText(match[2]) };
        }
        return { date: '', body: normalized };
    }

    function isDuplicateComment(comentarios, comentarioTexto) {
        const nuevo = getCommentParts(formatCommentText(comentarioTexto));
        const hoy = formatDateOnly(new Date());
        return (Array.isArray(comentarios) ? comentarios : []).some((texto) => {
            const existente = getCommentParts(texto);
            return existente.date === hoy && existente.body === nuevo.body;
        });
    }

    function mergeEmpleadoRecords(existing, incoming) {
        const merged = {
            ...existing,
            ...incoming,
            curp: normalizeCurp(incoming.curp || existing.curp),
            estatus: resolveStatus(existing.estatus, incoming.estatus),
            comentarios: [
                ...(Array.isArray(existing.comentarios) ? existing.comentarios : []),
                ...(Array.isArray(incoming.comentarios) ? incoming.comentarios : []),
            ],
            documentos: {
                ...(existing.documentos || {}),
                ...(incoming.documentos || {}),
            },
        };
        return normalizeEmpleado(merged);
    }

    function deduplicarPorCurp(empleados) {
        const mapa = new Map();
        (Array.isArray(empleados) ? empleados : []).forEach((item) => {
            const normalized = normalizeEmpleado(item);
            if (!normalized.curp) {
                mapa.set(`${Math.random()}-${Date.now()}`, normalized);
                return;
            }

            const existente = mapa.get(normalized.curp);
            if (!existente) {
                mapa.set(normalized.curp, normalized);
                return;
            }

            const preferirIncoming = parseFechaValor(normalized.fecha_ingreso) >= parseFechaValor(existente.fecha_ingreso);
            const combinado = preferirIncoming
                ? mergeEmpleadoRecords(existente, normalized)
                : mergeEmpleadoRecords(normalized, existente);
            mapa.set(normalized.curp, combinado);
        });

        return Array.from(mapa.values());
    }

    function safeParse(jsonText, fallback) {
        try {
            return JSON.parse(jsonText);
        } catch (_) {
            return fallback;
        }
    }

    function getEmpleados() {
        const parsed = safeParse(localStorage.getItem(STORAGE_KEY), []);
        return Array.isArray(parsed) ? parsed : [];
    }

    function setEmpleados(empleados) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(empleados));
    }

    function getMatchIndex(empleados, incoming) {
        const curp = normalizeCurp(incoming.curp);
        if (curp) {
            return empleados.findIndex((item) => normalizeCurp(item.curp) === curp);
        }

        const registroId = normalizeText(incoming.registro_id);
        if (registroId) {
            return empleados.findIndex((item) => normalizeText(item.registro_id) === registroId);
        }

        const nombre = normalizeText(incoming.nombre || incoming.nombre_completo).toUpperCase();
        if (!nombre) {
            return -1;
        }
        return empleados.findIndex((item) => {
            const itemNombre = normalizeText(item.nombre || item.nombre_completo).toUpperCase();
            return itemNombre === nombre;
        });
    }

    function normalizeEmpleado(empleado) {
        const source = empleado || {};
        const normalized = {
            ...source,
            curp: normalizeCurp(source.curp),
            estatus: normalizeStatus(source.estatus || 'ACTIVO'),
        };

        if (!Array.isArray(normalized.comentarios)) {
            normalized.comentarios = [];
        }

        if (!normalized.documentos || typeof normalized.documentos !== 'object') {
            normalized.documentos = {};
        }

        return normalized;
    }

    function upsertEmpleado(empleado) {
        const normalized = normalizeEmpleado(empleado);
        const empleados = getEmpleados();
        const index = getMatchIndex(empleados, normalized);

        if (index >= 0) {
            empleados[index] = mergeEmpleadoRecords(empleados[index], normalized);
        } else {
            empleados.push(normalized);
        }

        setEmpleados(deduplicarPorCurp(empleados));
        return empleados[index] || normalized;
    }

    function upsertMany(lista) {
        const empleados = getEmpleados();

        (Array.isArray(lista) ? lista : []).forEach((item) => {
            const normalized = normalizeEmpleado(item);
            const index = getMatchIndex(empleados, normalized);
            if (index >= 0) {
                empleados[index] = mergeEmpleadoRecords(empleados[index], normalized);
            } else {
                empleados.push(normalized);
            }
        });

        setEmpleados(deduplicarPorCurp(empleados));
    }

    function getEmpleadoByCurp(curp) {
        const target = normalizeCurp(curp);
        if (!target) return null;
        const empleados = getEmpleados();
        return empleados.find((item) => normalizeCurp(item.curp) === target) || null;
    }

    function getByCurp(curp) {
        return getEmpleadoByCurp(curp);
    }

    function getEmpleadoByIdentity(identity) {
        const empleados = getEmpleados();
        const index = getMatchIndex(empleados, identity || {});
        return index >= 0 ? empleados[index] : null;
    }

    function upsertByCurp(empleado) {
        return upsertEmpleado(empleado);
    }

    function updateEmpleadoByIdentity(identity, patch) {
        const empleados = getEmpleados();
        const index = getMatchIndex(empleados, identity || {});
        if (index < 0) return null;

        empleados[index] = normalizeEmpleado({
            ...empleados[index],
            ...(patch || {}),
        });
        setEmpleados(deduplicarPorCurp(empleados));
        return empleados[index];
    }

    function appendComentario(identity, comentarioTexto) {
        const empleados = getEmpleados();
        let index = getMatchIndex(empleados, identity || {});
        const comentarioFormateado = formatCommentText(comentarioTexto);

        const comentarios = index >= 0 && Array.isArray(empleados[index].comentarios)
            ? empleados[index].comentarios.slice()
            : [];

        if (isDuplicateComment(comentarios, comentarioTexto)) {
            return index >= 0 ? empleados[index] : null;
        }

        comentarios.push(comentarioFormateado);

        if (index >= 0) {
            empleados[index] = normalizeEmpleado({
                ...empleados[index],
                comentarios,
            });
        } else {
            const identidad = identity || {};
            const nuevoEmpleado = normalizeEmpleado({
                curp: normalizeCurp(identidad.curp),
                registro_id: normalizeText(identidad.registro_id),
                nombre: normalizeText(identidad.nombre),
                comentarios,
            });
            empleados.push(nuevoEmpleado);
            index = empleados.length - 1;
        }

        setEmpleados(deduplicarPorCurp(empleados));
        return empleados[index];
    }

    function appendComentarioByCurp(curp, comentarioTexto) {
        return appendComentario({ curp }, comentarioTexto);
    }

    function marcarInactivo(curpOrIdentity, motivo) {
        const identity = typeof curpOrIdentity === 'string'
            ? { curp: curpOrIdentity }
            : (curpOrIdentity || {});

        const motivoTexto = normalizeText(motivo);
        if (!motivoTexto) return null;

        const marca = `Motivo de baja: ${motivoTexto}`;
        const afterComment = appendComentario(identity, marca);
        if (!afterComment) return null;

        const empleado = updateEmpleadoByIdentity(identity, { estatus: 'INACTIVO' });
        actualizarVistas();
        return empleado;
    }

    function marcarActivo(curpOrIdentity, comentario, snapshot) {
        const identity = typeof curpOrIdentity === 'string'
            ? { curp: curpOrIdentity }
            : (curpOrIdentity || {});

        const textoComentario = normalizeText(comentario) || 'Reactivación sin comentario';
        const marca = `Reactivación: ${textoComentario}`;
        const afterComment = appendComentario(identity, marca);
        if (!afterComment) return null;

        const empleados = getEmpleados();
        const index = getMatchIndex(empleados, identity || {});
        if (index < 0) return null;

        const actual = empleados[index] || {};
        const patch = {
            ...actual,
            ...(snapshot || {}),
            estatus: 'ACTIVO',
            curp: normalizeCurp((snapshot && snapshot.curp) || actual.curp || identity.curp),
            registro_id: normalizeText((snapshot && snapshot.registro_id) || actual.registro_id || identity.registro_id),
            nombre: normalizeText((snapshot && snapshot.nombre) || actual.nombre || identity.nombre),
            nombre_completo: normalizeText((snapshot && snapshot.nombre_completo) || actual.nombre_completo || actual.nombre || identity.nombre),
            empresa: normalizeText((snapshot && snapshot.empresa) || actual.empresa || ''),
            puesto: normalizeText((snapshot && snapshot.puesto) || actual.puesto || ''),
            no_empleado: normalizeText((snapshot && snapshot.no_empleado) || actual.no_empleado || ''),
            fecha_ingreso: normalizeText((snapshot && snapshot.fecha_ingreso) || actual.fecha_ingreso || ''),
        };

        empleados[index] = normalizeEmpleado(patch);
        setEmpleados(deduplicarPorCurp(empleados));
        actualizarVistas();
        return empleados[index];
    }

    function saveDocumento(identity, docKey, fileRecord) {
        const empleados = getEmpleados();
        const index = getMatchIndex(empleados, identity || {});
        if (index < 0) return null;

        const documentos = Object.assign({}, empleados[index].documentos || {});
        documentos[docKey] = fileRecord;
        empleados[index].documentos = documentos;
        setEmpleados(empleados);
        return empleados[index];
    }

    function getDocumento(identity, docKey) {
        const empleados = getEmpleados();
        const index = getMatchIndex(empleados, identity || {});
        if (index < 0) return null;
        const docs = empleados[index].documentos || {};
        return docs[docKey] || null;
    }

    function formatDateTime(dateObj) {
        const date = dateObj instanceof Date ? dateObj : new Date();
        return date.toLocaleString('es-MX', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    function actualizarVistas() {
        localStorage.setItem(UPDATE_KEY, String(Date.now()));
        window.dispatchEvent(new CustomEvent('rh:data-updated', {
            detail: { updatedAt: Date.now() },
        }));
    }

    function subscribeVistas(callback) {
        if (typeof callback !== 'function') return () => {};

        const onStorage = (event) => {
            if (event.key === UPDATE_KEY) {
                callback();
            }
        };

        const onLocal = () => callback();

        window.addEventListener('storage', onStorage);
        window.addEventListener('rh:data-updated', onLocal);

        return function unsubscribe() {
            window.removeEventListener('storage', onStorage);
            window.removeEventListener('rh:data-updated', onLocal);
        };
    }

    const manager = {
        getEmpleados,
        upsertEmpleado,
        upsertMany,
        getEmpleadoByCurp,
        getByCurp,
        getEmpleadoByIdentity,
        upsertByCurp,
        updateEmpleadoByIdentity,
        appendComentario,
        appendComentarioByCurp,
        marcarInactivo,
        marcarActivo,
        saveDocumento,
        getDocumento,
        formatDateTime,
        actualizarVistas,
        subscribeVistas,
    };

    window.RHDataManager = manager;
    window.DataManager = manager;
    window.actualizarVistas = actualizarVistas;
})();