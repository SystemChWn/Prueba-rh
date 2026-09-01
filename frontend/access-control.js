(function () {
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