(function () {
    const storageKey = 'cwAccessUsers';
    window.SYSTEM_ACCESS = {
        usuario: 'admin',
        password: 'CwSistemas!',
    };

    const attendanceStorageKey = 'cwAttendanceSubmissions';

    function getTodayIso() {
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function readAttendanceMap() {
        try {
            const map = JSON.parse(localStorage.getItem(attendanceStorageKey) || '{}');
            return map && typeof map === 'object' ? map : {};
        } catch (_) {
            return {};
        }
    }

    function writeAttendanceMap(map) {
        localStorage.setItem(attendanceStorageKey, JSON.stringify(map));
    }

    function attendanceKey(area) {
        return `${String(area || '').trim().toUpperCase()}__${getTodayIso()}`;
    }

    function calcularTurnoActual() {
        const ahora = new Date();
        const minutosDelDia = ahora.getHours() * 60 + ahora.getMinutes();
        const inicioDia = 7 * 60 + 30;
        const inicioNoche = 19 * 60 + 30;
        return (minutosDelDia >= inicioDia && minutosDelDia < inicioNoche) ? 'DÍA' : 'NOCHE';
    }

    window.CwAttendance = {
        getTodayIso,
        calcularTurnoActual,
        submit(area, data) {
            const areaKey = String(area || '').trim();
            if (!areaKey) throw new Error('No hay área asignada para registrar la asistencia.');
            const map = readAttendanceMap();
            const record = { ...data, area: areaKey, fecha: getTodayIso(), registradoEn: new Date().toISOString() };
            map[attendanceKey(areaKey)] = record;
            writeAttendanceMap(map);
            return record;
        },
        isRegistered(area) {
            if (!String(area || '').trim()) return false;
            return Boolean(readAttendanceMap()[attendanceKey(area)]);
        },
        getSubmission(area) {
            return readAttendanceMap()[attendanceKey(area)] || null;
        },
        remove(area) {
            const map = readAttendanceMap();
            const key = attendanceKey(area);
            if (!(key in map)) return false;
            delete map[key];
            writeAttendanceMap(map);
            return true;
        },
        listTodaySubmissions() {
            const suffix = `__${getTodayIso()}`;
            return Object.entries(readAttendanceMap())
                .filter(([key]) => key.endsWith(suffix))
                .map(([, record]) => record);
        },
    };

    function readUsers() {
        try {
            const users = JSON.parse(localStorage.getItem(storageKey) || '[]');
            return Array.isArray(users) ? users : [];
        } catch (_) {
            return [];
        }
    }

    function writeUsers(users) {
        localStorage.setItem(storageKey, JSON.stringify(users));
    }

    function normalizePermissions(permissions) {
        const allowed = ['recursos_humanos', 'reclutamiento', 'control_asistencias', 'notificaciones'];
        return Array.isArray(permissions) ? permissions.filter((permission) => allowed.includes(permission)) : [];
    }

    function normalizeUser(data, existing = {}) {
        const permissions = normalizePermissions(data.permisos);
        return {
            id: existing.id || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
            usuario: String(data.usuario || '').trim(),
            password: String(data.password || '').trim(),
            permisos: permissions,
            area_responsable: permissions.includes('control_asistencias') ? String(data.area_responsable || '').trim() : '',
            nombre_notificacion: permissions.includes('notificaciones') ? String(data.nombre_notificacion || '').trim() : '',
            telefono_notificacion: permissions.includes('notificaciones') ? String(data.telefono_notificacion || '').trim() : '',
        };
    }

    function validateUser(user, users, userId = null) {
        if (!user.usuario || !user.password || !user.permisos.length) {
            throw new Error('Usuario, contraseña y al menos un permiso son obligatorios.');
        }
        if (users.some((item) => item.usuario.toLowerCase() === user.usuario.toLowerCase() && item.id !== userId)) {
            throw new Error('Ese usuario ya está registrado.');
        }
        if (user.permisos.includes('control_asistencias') && !user.area_responsable) {
            throw new Error('El área responsable es obligatoria para Control de Asistencias.');
        }
        if (user.permisos.includes('notificaciones') && (!user.nombre_notificacion || !user.telefono_notificacion)) {
            throw new Error('Nombre y teléfono son obligatorios para Notificaciones.');
        }
    }

    window.AccessControlUsers = {
        list() {
            return readUsers();
        },
        create(data) {
            const users = readUsers();
            const user = normalizeUser(data);
            validateUser(user, users);
            users.push(user);
            writeUsers(users);
            return user;
        },
        update(userId, data) {
            const users = readUsers();
            const index = users.findIndex((user) => user.id === userId);
            if (index < 0) throw new Error('Usuario no encontrado.');
            const user = normalizeUser(data, users[index]);
            validateUser(user, users, userId);
            users[index] = user;
            writeUsers(users);
            return user;
        },
        remove(userId) {
            const users = readUsers();
            const filteredUsers = users.filter((user) => user.id !== userId);
            if (filteredUsers.length === users.length) throw new Error('Usuario no encontrado.');
            writeUsers(filteredUsers);
        },
        authenticate(usuario, password) {
            return readUsers().find((user) => user.usuario === String(usuario).trim() && user.password === String(password).trim()) || null;
        },
    };

    const requiredPermissions = {
        'iniciorh.html': 'recursos_humanos',
        'Ingreso.html': 'recursos_humanos',
        'Cheong Woon.html': 'recursos_humanos',
        'Kronos.html': 'recursos_humanos',
        'asistencia.html': 'recursos_humanos',
        'InicioReclu.html': 'reclutamiento',
        'captura.html': 'control_asistencias',
    };

    const pageName = window.location.pathname.split('/').pop() || '';
    const requiredPermission = requiredPermissions[pageName];
    if (!requiredPermission) return;

    try {
        const accessUser = JSON.parse(sessionStorage.getItem('accesoUsuario') || '{}');
        const permissions = Array.isArray(accessUser.permisos) ? accessUser.permisos : [];
        if (!permissions.includes('sistemas') && !permissions.includes(requiredPermission)) {
            window.location.replace('portalrh.html');
        }
    } catch (_) {
        window.location.replace('portalrh.html');
    }
})();