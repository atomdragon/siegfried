/**
 * kwin_focus_watcher.js
 *
 * Script KWin event-driven para KDE Plasma bajo Wayland.
 *
 * REGLAS DE PRIVACIDAD:
 * - NO almacena títulos de ventanas ni URLs ni pestañas de navegador.
 * - Registra únicamente la clase/identificador de aplicación y la duración.
 */

(function () {
    var lastApp = "";
    var lastTimestamp = Date.now();

    function notifyFocusChange(app, durationSec) {
        if (!app) return;
        // Sanitizado local mínimo antes de emitir
        var cleanApp = app.toLowerCase();
        // Emisión hacia el daemon de Siegfried (a través de dbus o helper según configuración)
        print("SIEGFRIED_FOCUS_EVENT: app=" + cleanApp + " duration=" + durationSec);
    }

    workspace.windowActivated.connect(function (client) {
        if (!client) return;

        var now = Date.now();
        var elapsedSec = (now - lastTimestamp) / 1000.0;

        if (lastApp && elapsedSec > 0.5) {
            notifyFocusChange(lastApp, elapsedSec);
        }

        // Obtener solo el recurso de la aplicación (ej. "firefox", "code", "konsole")
        var currentApp = client.resourceClass ? client.resourceClass.toString() : (client.resourceName ? client.resourceName.toString() : "unknown");

        lastApp = currentApp;
        lastTimestamp = now;
    });
})();
